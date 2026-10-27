"""
foamTimes - OpenFOAM case timestep utilities.

Find (and optionally delete) timesteps in the two "sides" of a case:
  root        time directories in the case root (undecomposed or reconstructed)
  decomposed  processorN/ dirs, or collated processorsN/ (processorsN_a-b) dirs

Decomposed timesteps are the UNION of all processor dirs -- safe for partial
writes where some processors may be missing a timestep. Listing shows every
side that has timesteps; deleting from a case where both sides have timesteps
requires --root, --decomposed, or both.

Usage:
    foamTimes [OPTIONS] [CASE_DIR]

Examples:
    foamTimes                        # list latest time, current dir
    foamTimes /path/to/case          # list latest time, given case dir
    foamTimes --all                  # list all times (union)
    foamTimes --all --verbose        # show which side/processors have each time
    foamTimes --rm                   # delete latest time
    foamTimes --all --rm             # delete ALL times (except 0)
    foamTimes --time 1.5 --rm        # delete a specific time
    foamTimes --rm --dry-run         # preview deletion without executing
    foamTimes --keep-last 3          # show which times would be kept/deleted
    foamTimes --keep-last 3 --rm     # keep only the 3 newest complete times
    foamTimes --decomposed --keep-last 2 --rm  # after reconstructPar: prune processor dirs
    foamTimes --root --keep-last 2 --rm        # prune reconstructed times in the case root
    foamTimes --root --decomposed --time 8 --rm  # delete 8 from both sides
"""

import argparse
import shutil
import sys
from pathlib import Path

from pyfoamdecomp.case import (
    all_locations, find_processor_families, find_time_by_value, get_times_union,
    is_complete, is_time_dir, side_label,
)
from pyfoamdecomp.cli import fail


# ---------------------------------------------------------------------------
# Side and timestep selection
# ---------------------------------------------------------------------------

def split_keep_last(times, sides, n):
    """
    Split sorted times into (keep, delete) so that the newest n COMPLETE
    timesteps (see is_complete) are kept. Partial timesteps newer than the
    oldest kept one are also kept; nothing is deleted if there are n or fewer
    complete timesteps.
    """
    complete = [t for t in times if is_complete(t, sides)]
    if len(complete) <= n:
        return list(times), []
    cutoff = float(complete[-n])
    keep = [t for t in times if float(t) >= cutoff]
    delete = [t for t in times if float(t) < cutoff]
    return keep, delete


def resolve_sides(case_dir, root, decomposed, deleting, with_zero):
    """
    Return the sides to act on, or exit with an error. Without --root or
    --decomposed, listing uses every side with timesteps, while deleting
    requires the choice to be unambiguous (never falls back to the root).
    """
    families = find_processor_families(case_dir)
    with_times = {k: d for k, d in families.items() if get_times_union([d], include_zero=True)}
    root_side = [case_dir]

    def decomposed_side():
        if len(with_times) > 1:
            labels = ', '.join(side_label(d) for _, d in sorted(with_times.items()))
            fail(f"Several processor dir layouts contain timesteps: {labels}")
        return next(iter(with_times.values()), None)

    if root or decomposed:
        sides = [root_side] if root else []
        if decomposed:
            dec = decomposed_side()
            if dec is None:
                fail(f"No processor* directories with timesteps found in {case_dir}")
            sides.append(dec)
        return sides

    if not families:
        return [root_side]

    dec = decomposed_side()
    root_has_times = bool(get_times_union([root_side], include_zero=with_zero))
    if not deleting:
        return ([root_side] if root_has_times else []) + ([dec] if dec else [])
    if dec and root_has_times:
        fail(f"Timesteps exist in both the case root and {side_label(dec)}; pass --root, "
             "--decomposed, or both")
    if root_has_times:
        fail("processor* directories contain no timesteps, but the case root does; pass --root")
    return [dec] if dec else []


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def presence_status(t, sides):
    """One column per side (blank if t is absent there), then any missing processors."""
    cells, missing = [], []
    for side in sides:
        label = side_label(side)
        absent = [loc.name for loc in side if not (loc / t).is_dir()]
        n_present = len(side) - len(absent)
        if len(side) == 1:
            cell, width = (label if n_present else ''), len(label)
        else:
            cell = f'{n_present}/{len(side)} {label}' if n_present else ''
            width = len(f'{len(side)}/{len(side)} {label}')
            if n_present:
                missing += absent
        cells.append(cell.ljust(width))
    status = '  ' + '  '.join(cells)
    if missing:
        suffix = '...' if len(missing) > 5 else ''
        status += f"  [MISSING: {', '.join(missing[:5])}{suffix}]"
    return status


def list_times(times, sides, verbose):
    width = max(len(t) for t in times)
    for t in times:
        if verbose:
            print(f"{t.ljust(width)}{presence_status(t, sides)}".rstrip())
        else:
            print(t)


def list_keep_last(keep, delete, sides, verbose):
    width = max(len(t) for t in keep + delete)
    for t in sorted(keep + delete, key=float):
        line = f"{t.ljust(width)}  {'keep' if t in keep else 'delete':<6}"
        if verbose:
            line += presence_status(t, sides)
        elif t in keep and not is_complete(t, sides):
            line += "  (partial)"
        print(line.rstrip())


def delete_times(times, sides, dry_run, verbose):
    if not times:
        print("Nothing to delete.")
        return

    for t in times:
        for loc in all_locations(sides):
            target = loc / t
            if target.is_dir():
                if dry_run:
                    print(f"[dry-run] Would delete: {target}")
                else:
                    shutil.rmtree(target)
                    if verbose:
                        print(f"Deleted: {target}")

    if not dry_run:
        print(f"Deleted time(s): {', '.join(times)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def positive_int(value):
    try:
        n = int(value)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got '{value}'")
    return n


def build_parser():
    parser = argparse.ArgumentParser(
        prog='foamTimes',
        description=(
            'List or remove OpenFOAM timesteps in the case root and/or decomposed\n'
            'processor* (or collated processorsN) directories.\n'
            'Uses the UNION of all processors -- safe for partial writes.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split('Usage:')[1],
    )
    parser.add_argument(
        'case_dir',
        nargs='?',
        default='.',
        metavar='CASE_DIR',
        help='Path to the OpenFOAM case directory (default: current directory)',
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        '--all',
        action='store_true',
        help='Act on ALL timesteps instead of just the latest',
    )
    selection.add_argument(
        '--time', '-t',
        metavar='TIME',
        help='Act on a specific timestep value, matched numerically (e.g. --time 1.5)',
    )
    selection.add_argument(
        '--keep-last',
        type=positive_int,
        metavar='N',
        help=(
            'Act on all timesteps except the N newest complete ones '
            '(in the root, or on every processor); newer partial timesteps are also kept'
        ),
    )
    parser.add_argument(
        '--root',
        action='store_true',
        help='Use the time directories in the case root (combine with --decomposed for both)',
    )
    parser.add_argument(
        '--decomposed',
        action='store_true',
        help='Use the time directories in the processor* / processorsN dirs',
    )
    parser.add_argument(
        '--rm',
        action='store_true',
        help='Delete the selected timestep(s)',
    )
    parser.add_argument(
        '--dry-run', '-n',
        action='store_true',
        help='Preview what would be deleted without actually deleting',
    )
    parser.add_argument(
        '--with-zero',
        action='store_true',
        help='Include the 0/ directory in the timestep list',
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Show which side/processors have each timestep',
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    case_dir = Path(args.case_dir).resolve()
    if not case_dir.is_dir():
        fail(f"Case directory not found: {case_dir}")

    deleting = args.rm or args.dry_run
    sides = resolve_sides(case_dir, args.root, args.decomposed, deleting, args.with_zero)
    if args.verbose and sides:
        names = ', '.join(
            'case root' if s == [case_dir] else
            side_label(s) + ('' if len(s) == 1 else f' ({len(s)} dirs)') for s in sides)
        print(f"Using time directories in: {names}", file=sys.stderr)
    list_verbose = args.verbose and len(all_locations(sides)) > 1

    if args.time:
        if not is_time_dir(args.time):
            fail(f"'{args.time}' doesn't look like a valid timestep name.")
        times = find_time_by_value(args.time, sides)
        if not times:
            fail(f"No timestep matching '{args.time}'")
    else:
        times = get_times_union(sides, include_zero=args.with_zero)
        if not times:
            print("No timesteps found.")
            return
        if args.keep_last:
            keep, delete = split_keep_last(times, sides, args.keep_last)
            if deleting:
                delete_times(delete, sides, dry_run=args.dry_run, verbose=args.verbose)
                label = "[dry-run] Would keep" if args.dry_run else "Kept time(s)"
                print(f"{label}: {', '.join(keep)}")
            else:
                list_keep_last(keep, delete, sides, verbose=list_verbose)
            return
        if not args.all:
            times = times[-1:]

    if deleting:
        delete_times(times, sides, dry_run=args.dry_run, verbose=args.verbose)
    else:
        list_times(times, sides, verbose=list_verbose)


if __name__ == '__main__':
    main()
