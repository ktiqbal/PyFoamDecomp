#!/usr/bin/env python3
"""
foamTimes.py - OpenFOAM decomposed case timestep utilities.

Find (and optionally delete) timesteps across processor* directories,
using the UNION of all processors -- safe for partial writes where some
processors may be missing a timestep.

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
    dirs = sorted(
        p for p in case_dir.iterdir()
        if p.is_dir() and re.fullmatch(r'processors?[0-9]+(_[0-9]+-[0-9]+)?', p.name)
    )
    if not dirs:
        print("ERROR: No processor* directories found in {}".format(case_dir), file=sys.stderr)
        sys.exit(1)
    return dirs


def get_times_union(proc_dirs, include_zero=False):
    """
    Return sorted list of all timestep names present in ANY processor dir
    (i.e. the union), optionally including '0'.
    """
    times = set()
    for proc in proc_dirs:
        for entry in proc.iterdir():
            if entry.is_dir() and is_time_dir(entry.name):
                if not include_zero and entry.name == '0':
                    continue
                times.add(entry.name)
    return sorted(times, key=time_sort_key)


def get_latest_time(proc_dirs, include_zero=False):
    """Return the single latest timestep name across all processor dirs."""
    times = get_times_union(proc_dirs, include_zero=include_zero)
    return times[-1] if times else None


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def list_times(times, proc_dirs, verbose):
    for t in times:
        if verbose:
            present = [p.name for p in proc_dirs if (p / t).is_dir()]
            missing = [p.name for p in proc_dirs if not (p / t).is_dir()]
            status = "  present in {}/{} processors".format(len(present), len(proc_dirs))
            if missing:
                truncated = missing[:5]
                suffix = '...' if len(missing) > 5 else ''
                status += "  [MISSING: {}{}]".format(', '.join(truncated), suffix)
            print("{}{}".format(t, status))
        else:
            print(t)


def delete_times(times, proc_dirs, dry_run, verbose):
    if not times:
        print("Nothing to delete.")
        return

    for t in times:
        for proc in proc_dirs:
            target = proc / t
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

def build_parser():
    parser = argparse.ArgumentParser(
        prog='foamTimes',
        description=(
            'List or remove OpenFOAM timesteps across decomposed processor* directories.\n'
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
    parser.add_argument(
        '--all',
        action='store_true',
        help='Act on ALL timesteps instead of just the latest',
    )
    parser.add_argument(
        '--time', '-t',
        metavar='TIME',
        help='Act on a specific timestep value (e.g. --time 1.5)',
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

    proc_dirs = find_processor_dirs(case_dir)

    # --- Determine which times to act on ---
    if args.time:
        if not is_time_dir(args.time):
            print("ERROR: '{}' doesn't look like a valid timestep name.".format(args.time), file=sys.stderr)
            sys.exit(1)
        times = [args.time]
    elif args.all:
        times = get_times_union(proc_dirs, include_zero=args.with_zero)
        if not times:
            print("No timesteps found.")
            sys.exit(0)
    else:
        latest = get_latest_time(proc_dirs, include_zero=args.with_zero)
        if latest is None:
            print("No timesteps found.")
            sys.exit(0)
        times = [latest]

    # --- Act ---
    if args.rm or args.dry_run:
        delete_times(times, proc_dirs, dry_run=args.dry_run, verbose=args.verbose)
    else:
        list_times(times, proc_dirs, verbose=args.verbose)


if __name__ == '__main__':
    main()
