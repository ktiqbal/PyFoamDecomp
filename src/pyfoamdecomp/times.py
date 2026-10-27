#!/usr/bin/env python3
"""
foamTimes.py - OpenFOAM case timestep utilities.

Find (and optionally delete) timesteps in the two "sides" of a case:
  root        time directories in the case root (undecomposed or reconstructed)
  decomposed  processorN/ dirs, or collated processorsN/ (processorsN_a-b) dirs

Decomposed timesteps are the UNION of all processor dirs -- safe for partial
writes where some processors may be missing a timestep. Listing shows every
side that has timesteps; deleting from a case where both sides have timesteps
requires --root, --decomposed, or both.

Usage:
    foamTimes.py [OPTIONS] [CASE_DIR]

Examples:
    foamTimes.py                        # list latest time, current dir
    foamTimes.py /path/to/case          # list latest time, given case dir
    foamTimes.py --all                  # list all times (union)
    foamTimes.py --all --verbose        # show which side/processors have each time
    foamTimes.py --rm                   # delete latest time
    foamTimes.py --all --rm             # delete ALL times (except 0)
    foamTimes.py --time 1.5 --rm        # delete a specific time
    foamTimes.py --rm --dry-run         # preview deletion without executing
    foamTimes.py --keep-last 3          # show which times would be kept/deleted
    foamTimes.py --keep-last 3 --rm     # keep only the 3 newest complete times
    foamTimes.py --decomposed --keep-last 2 --rm  # after reconstructPar: prune processor dirs
    foamTimes.py --root --keep-last 2 --rm        # prune reconstructed times in the case root
    foamTimes.py --root --decomposed --time 8 --rm  # delete 8 from both sides
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

PROCESSOR_DIR_RE = r'(processors?)([0-9]+)(_[0-9]+-[0-9]+)?'


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------
# A "side" is a list of directories holding time directories: [case_dir] for
# the root, or the processor dirs of one family. Helpers take a list of sides.

def is_time_dir(name):
    """Return True if the directory name looks like an OpenFOAM timestep."""
    # Matches: 0, 1, 0.5, 1.5, 1e-3, 1.5e+2, etc.  Excludes 'constant', 'system', etc.
    return bool(re.fullmatch(r'[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?', name))


def time_sort_key(name):
    """Convert a timestep directory name to float for numeric sorting."""
    try:
        return float(name)
    except ValueError:
        return 0.0


def processor_dir_key(path):
    """Numeric sort key, so processor2 sorts before processor10 and processors4_2-3 after _0-1."""
    _, n, ranks = re.fullmatch(PROCESSOR_DIR_RE, path.name).groups()
    return int(n), int(ranks[1:].split('-')[0]) if ranks else -1


def find_processor_families(case_dir):
    """
    Group processor dirs by layout: classic processorN dirs form one family,
    collated processorsN and processorsN_a-b dirs form one family per N.
    """
    families = {}
    for p in case_dir.iterdir():
        m = re.fullmatch(PROCESSOR_DIR_RE, p.name)
        if m and p.is_dir():
            key = 'processor' if m.group(1) == 'processor' else 'processors' + m.group(2)
            families.setdefault(key, []).append(p)
    return {k: sorted(dirs, key=processor_dir_key) for k, dirs in families.items()}


def side_label(side):
    """'root', a single processor dir's name, or a family glob like 'processor*'."""
    m = re.fullmatch(PROCESSOR_DIR_RE, side[0].name)
    if m is None:
        return 'root'
    if len(side) == 1:
        return side[0].name
    return 'processor*' if m.group(1) == 'processor' else 'processors{}_*'.format(m.group(2))


def all_locations(sides):
    return [loc for side in sides for loc in side]


def get_times_union(sides, include_zero=False):
    """
    Return sorted list of all timestep names present in ANY location
    (i.e. the union), optionally including '0'.
    """
    times = set()
    for loc in all_locations(sides):
        for entry in loc.iterdir():
            if entry.is_dir() and is_time_dir(entry.name):
                if not include_zero and entry.name == '0':
                    continue
                times.add(entry.name)
    return sorted(times, key=time_sort_key)


def get_latest_time(sides, include_zero=False):
    """Return the single latest timestep name across all sides."""
    times = get_times_union(sides, include_zero=include_zero)
    return times[-1] if times else None


def find_time_by_value(value, sides):
    """Return the timestep names whose numeric value equals value (e.g. '8.0' -> ['8'])."""
    return [t for t in get_times_union(sides, include_zero=True) if float(t) == float(value)]


def is_complete(t, sides):
    """True if some side has t in every one of its locations."""
    return any(all((loc / t).is_dir() for loc in side) for side in sides)


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
    cutoff = time_sort_key(complete[-n])
    keep = [t for t in times if time_sort_key(t) >= cutoff]
    delete = [t for t in times if time_sort_key(t) < cutoff]
    return keep, delete


def fail(message):
    print("ERROR: {}".format(message), file=sys.stderr)
    sys.exit(1)


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
            fail("Several processor dir layouts contain timesteps: {}".format(
                ', '.join(side_label(d) for _, d in sorted(with_times.items()))))
        return next(iter(with_times.values()), None)

    if root or decomposed:
        sides = [root_side] if root else []
        if decomposed:
            dec = decomposed_side()
            if dec is None:
                fail("No processor* directories with timesteps found in {}".format(case_dir))
            sides.append(dec)
        return sides

    if not families:
        return [root_side]

    dec = decomposed_side()
    root_has_times = bool(get_times_union([root_side], include_zero=with_zero))
    if not deleting:
        return ([root_side] if root_has_times else []) + ([dec] if dec else [])
    if dec and root_has_times:
        fail("Timesteps exist in both the case root and {}; pass --root, --decomposed, "
             "or both".format(side_label(dec)))
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
            cell = '{}/{} {}'.format(n_present, len(side), label) if n_present else ''
            width = len('{0}/{0} {1}'.format(len(side), label))
            if n_present:
                missing += absent
        cells.append(cell.ljust(width))
    status = '  ' + '  '.join(cells)
    if missing:
        suffix = '...' if len(missing) > 5 else ''
        status += "  [MISSING: {}{}]".format(', '.join(missing[:5]), suffix)
    return status


def list_times(times, sides, verbose):
    width = max(len(t) for t in times)
    for t in times:
        if verbose:
            print("{}{}".format(t.ljust(width), presence_status(t, sides)).rstrip())
        else:
            print(t)


def list_keep_last(keep, delete, sides, verbose):
    width = max(len(t) for t in keep + delete)
    for t in sorted(keep + delete, key=time_sort_key):
        line = "{}  {:<6}".format(t.ljust(width), 'keep' if t in keep else 'delete')
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
                    print("[dry-run] Would delete: {}".format(target))
                else:
                    shutil.rmtree(str(target))
                    if verbose:
                        print("Deleted: {}".format(target))
            # silently skip locations that don't have this timestep (partial write)

    if not dry_run:
        print("Deleted time(s): {}".format(', '.join(times)))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def positive_int(value):
    try:
        n = int(value)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError("must be a positive integer, got '{}'".format(value))
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


def main():
    parser = build_parser()
    args = parser.parse_args()

    case_dir = Path(args.case_dir).resolve()
    if not case_dir.is_dir():
        fail("Case directory not found: {}".format(case_dir))

    deleting = args.rm or args.dry_run
    sides = resolve_sides(case_dir, args.root, args.decomposed, deleting, args.with_zero)
    if args.verbose and sides:
        names = ', '.join(
            'case root' if s == [case_dir] else
            side_label(s) + ('' if len(s) == 1 else ' ({} dirs)'.format(len(s))) for s in sides)
        print("Using time directories in: {}".format(names), file=sys.stderr)
    list_verbose = args.verbose and len(all_locations(sides)) > 1

    if args.keep_last:
        times = get_times_union(sides, include_zero=args.with_zero)
        if not times:
            print("No timesteps found.")
            sys.exit(0)
        keep, delete = split_keep_last(times, sides, args.keep_last)
        if deleting:
            delete_times(delete, sides, dry_run=args.dry_run, verbose=args.verbose)
            label = "[dry-run] Would keep" if args.dry_run else "Kept time(s)"
            print("{}: {}".format(label, ', '.join(keep)))
        else:
            list_keep_last(keep, delete, sides, verbose=list_verbose)
        return

    # --- Determine which times to act on ---
    if args.time:
        if not is_time_dir(args.time):
            fail("'{}' doesn't look like a valid timestep name.".format(args.time))
        times = find_time_by_value(args.time, sides)
        if not times:
            fail("No timestep matching '{}'".format(args.time))
    elif args.all:
        times = get_times_union(sides, include_zero=args.with_zero)
        if not times:
            print("No timesteps found.")
            sys.exit(0)
    else:
        latest = get_latest_time(sides, include_zero=args.with_zero)
        if latest is None:
            print("No timesteps found.")
            sys.exit(0)
        times = [latest]

    # --- Act ---
    if deleting:
        delete_times(times, sides, dry_run=args.dry_run, verbose=args.verbose)
    else:
        list_times(times, sides, verbose=list_verbose)


if __name__ == '__main__':
    main()
