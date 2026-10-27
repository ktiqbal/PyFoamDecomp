#!/usr/bin/env python3
"""
foamTimes.py - OpenFOAM case timestep utilities.

Find (and optionally delete) timesteps across processor* directories,
using the UNION of all processors -- safe for partial writes where some
processors may be missing a timestep. Cases without processor* directories
(undecomposed) use the time directories in the case root instead; --root
forces this for decomposed cases too (e.g. reconstructed times).

Usage:
    foamTimes.py [OPTIONS] [CASE_DIR]

Examples:
    foamTimes.py                        # list latest time, current dir
    foamTimes.py /path/to/case          # list latest time, given case dir
    foamTimes.py --all                  # list all times (union)
    foamTimes.py --rm                   # delete latest time
    foamTimes.py --all --rm             # delete ALL times (except 0)
    foamTimes.py --time 1.5 --rm        # delete a specific time
    foamTimes.py --rm --dry-run         # preview deletion without executing
    foamTimes.py --keep-last 3          # show which times would be kept/deleted
    foamTimes.py --keep-last 3 --rm     # keep only the 3 newest complete times
    foamTimes.py --root --keep-last 2 --rm  # prune reconstructed times in the case root
    foamTimes.py --verbose              # show per-processor breakdown
"""

import argparse
import re
import shutil
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

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


def find_processor_dirs(case_dir):
    """Return sorted list of processor* directories in the case directory."""
    return sorted(
        p for p in case_dir.iterdir()
        if p.is_dir() and re.fullmatch(r'processors?[0-9]+(_[0-9]+-[0-9]+)?', p.name)
    )


def find_locations(case_dir, root):
    """
    Return the directories that hold time directories: the processor* dirs,
    or [case_dir] if root is set or the case is not decomposed.
    """
    if root:
        return [case_dir]
    return find_processor_dirs(case_dir) or [case_dir]


def get_times_union(locations, include_zero=False):
    """
    Return sorted list of all timestep names present in ANY location
    (i.e. the union), optionally including '0'.
    """
    times = set()
    for loc in locations:
        for entry in loc.iterdir():
            if entry.is_dir() and is_time_dir(entry.name):
                if not include_zero and entry.name == '0':
                    continue
                times.add(entry.name)
    return sorted(times, key=time_sort_key)


def get_latest_time(locations, include_zero=False):
    """Return the single latest timestep name across all locations."""
    times = get_times_union(locations, include_zero=include_zero)
    return times[-1] if times else None


def find_time_by_value(value, locations):
    """Return the timestep names whose numeric value equals value (e.g. '8.0' -> ['8'])."""
    return [t for t in get_times_union(locations, include_zero=True) if float(t) == float(value)]


def is_complete(t, locations):
    return all((loc / t).is_dir() for loc in locations)


def split_keep_last(times, locations, n):
    """
    Split sorted times into (keep, delete) so that the newest n COMPLETE
    timesteps (present in every location) are kept. Partial timesteps newer
    than the oldest kept one are also kept; nothing is deleted if there are
    n or fewer complete timesteps.
    """
    complete = [t for t in times if is_complete(t, locations)]
    if len(complete) <= n:
        return list(times), []
    cutoff = time_sort_key(complete[-n])
    keep = [t for t in times if time_sort_key(t) >= cutoff]
    delete = [t for t in times if time_sort_key(t) < cutoff]
    return keep, delete


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def presence_status(t, locations):
    present = [loc.name for loc in locations if (loc / t).is_dir()]
    missing = [loc.name for loc in locations if not (loc / t).is_dir()]
    status = "  present in {}/{} processors".format(len(present), len(locations))
    if missing:
        truncated = missing[:5]
        suffix = '...' if len(missing) > 5 else ''
        status += "  [MISSING: {}{}]".format(', '.join(truncated), suffix)
    return status


def list_times(times, locations, verbose):
    for t in times:
        if verbose:
            print("{}{}".format(t, presence_status(t, locations)))
        else:
            print(t)


def list_keep_last(keep, delete, locations, verbose):
    width = max(len(t) for t in keep + delete)
    for t in sorted(keep + delete, key=time_sort_key):
        line = "{}  {:<6}".format(t.ljust(width), 'keep' if t in keep else 'delete')
        if verbose:
            line += presence_status(t, locations)
        elif t in keep and not is_complete(t, locations):
            line += "  (partial)"
        print(line.rstrip())


def delete_times(times, locations, dry_run, verbose):
    if not times:
        print("Nothing to delete.")
        return

    for t in times:
        for loc in locations:
            target = loc / t
            if target.is_dir():
                if dry_run:
                    print("[dry-run] Would delete: {}".format(target))
                else:
                    shutil.rmtree(str(target))
                    if verbose:
                        print("Deleted: {}".format(target))
            # silently skip processors that don't have this timestep (partial write)

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
            'List or remove OpenFOAM timesteps across decomposed processor* directories,\n'
            'or in the case root if the case is not decomposed (or with --root).\n'
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
            '(present on every processor); newer partial timesteps are also kept'
        ),
    )
    parser.add_argument(
        '--root',
        action='store_true',
        help='Use time directories in the case root even if processor* dirs exist',
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
        help='Show per-processor detail (which processors have each timestep)',
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    case_dir = Path(args.case_dir).resolve()
    if not case_dir.is_dir():
        print("ERROR: Case directory not found: {}".format(case_dir), file=sys.stderr)
        sys.exit(1)

    locations = find_locations(case_dir, args.root)
    root_mode = locations == [case_dir]
    if args.verbose:
        source = "case root" if root_mode else "processor* dirs ({})".format(len(locations))
        print("Using time directories in {}".format(source), file=sys.stderr)
    list_verbose = args.verbose and not root_mode

    if args.keep_last:
        times = get_times_union(locations, include_zero=args.with_zero)
        if not times:
            print("No timesteps found.")
            sys.exit(0)
        keep, delete = split_keep_last(times, locations, args.keep_last)
        if args.rm or args.dry_run:
            delete_times(delete, locations, dry_run=args.dry_run, verbose=args.verbose)
            label = "[dry-run] Would keep" if args.dry_run else "Kept time(s)"
            print("{}: {}".format(label, ', '.join(keep)))
        else:
            list_keep_last(keep, delete, locations, verbose=list_verbose)
        return

    # --- Determine which times to act on ---
    if args.time:
        if not is_time_dir(args.time):
            print("ERROR: '{}' doesn't look like a valid timestep name.".format(args.time), file=sys.stderr)
            sys.exit(1)
        times = find_time_by_value(args.time, locations)
        if not times:
            print("ERROR: No timestep matching '{}'".format(args.time), file=sys.stderr)
            sys.exit(1)
    elif args.all:
        times = get_times_union(locations, include_zero=args.with_zero)
        if not times:
            print("No timesteps found.")
            sys.exit(0)
    else:
        latest = get_latest_time(locations, include_zero=args.with_zero)
        if latest is None:
            print("No timesteps found.")
            sys.exit(0)
        times = [latest]

    # --- Act ---
    if args.rm or args.dry_run:
        delete_times(times, locations, dry_run=args.dry_run, verbose=args.verbose)
    else:
        list_times(times, locations, verbose=list_verbose)


if __name__ == '__main__':
    main()
