#!/usr/bin/env python3
"""
foamLogParse.py  —  Parse OpenFOAM log files for post-processing and plotting.

Extracts per-timestep data and writes whitespace-separated .txt files that
load cleanly with numpy.loadtxt, pandas.read_csv(sep='\\s+'), or gnuplot.

Extracted quantities
--------------------
  courant.txt          — simulation time, deltaT, mean and max Courant numbers
  continuity.txt       — continuity error: sum-local, global, cumulative
  timing.txt           — ExecutionTime and ClockTime per step
  residuals_<field>.txt — per-field: first initial residual, last final
                          residual, total solver iterations

When a field is solved multiple times per step (e.g. pressure in PISO/PIMPLE
corrector loops) the *first* initial residual and the *last* final residual are
recorded, and iteration counts are summed.

Usage
-----
    python3 foamLogParse.py <logfile>
    python3 foamLogParse.py <logfile> --out results/
    python3 foamLogParse.py <logfile> --prefix run1_ --verbose
    python3 foamLogParse.py <logfile> --no-continuity --no-timing
"""

import re
import os
import sys
import argparse

# ---------------------------------------------------------------------------
# Regular expressions
# ---------------------------------------------------------------------------

# "Courant Number mean: 0.000181 max: 0.009999"
# Also matches "Interface Courant Number mean: ..."
RE_COURANT = re.compile(
    r'Courant Number mean:\s*(\S+)\s+max:\s*(\S+)'
)

# "deltaT = 8.594e-05"
RE_DELTA_T = re.compile(r'^deltaT\s*=\s*(\S+)')

# "Time = 6.000089317481708"
RE_TIME = re.compile(r'^Time\s*=\s*(\S+)')

# "DILUPBiCG:  Solving for Ux, Initial residual = 6.95e-07, Final residual = 8.42e-09, No Iterations 7"
# "smoothSolver:  Solving for ...", "GAMG:  Solving for ..."
RE_SOLVER = re.compile(
    r'^\S+:\s+Solving for (\S+),\s+'
    r'Initial residual\s*=\s*(\S+),\s+'
    r'Final residual\s*=\s*(\S+),\s+'
    r'No Iterations\s+(\d+)'
)

# "time step continuity errors : sum local = 1.44e-12, global = -1.80e-14, cumulative = -1.80e-14"
RE_CONTINUITY = re.compile(
    r'^time step continuity errors\s*:\s+'
    r'sum local\s*=\s*(\S+),\s+'
    r'global\s*=\s*(\S+),\s+'
    r'cumulative\s*=\s*(\S+)'
)

# "ExecutionTime = 1.84 s  ClockTime = 2 s"
RE_EXEC_TIME = re.compile(
    r'^ExecutionTime\s*=\s*(\S+)\s+s\s+ClockTime\s*=\s*(\S+)\s+s'
)

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_log(path):
    """
    Single-pass streaming parse of an OpenFOAM log file.

    Parameters
    ----------
    path : str
        Absolute path to the log file.

    Returns
    -------
    records : list of dict
        One dict per completed (or in-progress) time step with keys:
          'time', 'delta_t', 'co_mean', 'co_max',
          'exec_time', 'clock_time',
          'cont_sum_local', 'cont_global', 'cont_cumulative',
          'fields': { field_name: {'init': float, 'final': float, 'iters': int} }
    fields_order : list of str
        All field names seen, in order of first appearance.
    """

    records      = []
    fields_order = []
    seen_fields  = set()

    # Courant and deltaT appear BEFORE "Time = T" in the log.
    # Buffer them until we hit the Time line.
    pending_co_mean  = None
    pending_co_max   = None
    pending_delta_t  = None

    # Accumulator for the current time step
    in_step          = False
    cur_time         = None
    cur_co_mean      = None
    cur_co_max       = None
    cur_delta_t      = None
    cur_fields       = {}   # field -> {'init', 'final', 'iters'}
    cur_cont         = None # (sum_local, global, cumulative)
    cur_exec         = None # (exec_time, clock_time)

    def flush():
        """Package current accumulators into a record and append."""
        records.append({
            'time':            cur_time,
            'delta_t':         cur_delta_t,
            'co_mean':         cur_co_mean,
            'co_max':          cur_co_max,
            'cont_sum_local':  cur_cont[0] if cur_cont else None,
            'cont_global':     cur_cont[1] if cur_cont else None,
            'cont_cumulative': cur_cont[2] if cur_cont else None,
            'exec_time':       cur_exec[0] if cur_exec else None,
            'clock_time':      cur_exec[1] if cur_exec else None,
            'fields':          dict(cur_fields),
        })

    with open(path, 'r', errors='replace') as fh:
        for line in fh:
            line = line.rstrip('\n')

            # ---- Courant number (buffered; appears before Time =) ----------
            m = RE_COURANT.search(line)   # .search so "Interface Courant" also matches
            if m:
                pending_co_mean = float(m.group(1))
                pending_co_max  = float(m.group(2))
                continue

            # ---- deltaT (appears just before Time =) -----------------------
            m = RE_DELTA_T.match(line)
            if m:
                pending_delta_t = float(m.group(1))
                continue

            # ---- New time step ---------------------------------------------
            m = RE_TIME.match(line)
            if m:
                if in_step:
                    flush()

                cur_time    = float(m.group(1))
                cur_co_mean = pending_co_mean
                cur_co_max  = pending_co_max
                cur_delta_t = pending_delta_t
                cur_fields  = {}
                cur_cont    = None
                cur_exec    = None
                in_step     = True

                # Reset pending buffers
                pending_co_mean = None
                pending_co_max  = None
                pending_delta_t = None
                continue

            # Lines below are only meaningful inside a time step
            if not in_step:
                continue

            # ---- Linear solver residuals -----------------------------------
            m = RE_SOLVER.match(line)
            if m:
                field     = m.group(1)
                init_res  = float(m.group(2))
                final_res = float(m.group(3))
                n_iter    = int(m.group(4))

                if field not in seen_fields:
                    seen_fields.add(field)
                    fields_order.append(field)

                if field not in cur_fields:
                    # First solve for this field this step: record init residual
                    cur_fields[field] = {
                        'init':  init_res,
                        'final': final_res,
                        'iters': n_iter,
                    }
                else:
                    # Subsequent solve (pressure correctors, outer iters):
                    # keep first init, update final, accumulate iters
                    cur_fields[field]['final']  = final_res
                    cur_fields[field]['iters'] += n_iter
                continue

            # ---- Continuity errors -----------------------------------------
            m = RE_CONTINUITY.match(line)
            if m:
                cur_cont = (float(m.group(1)),
                            float(m.group(2)),
                            float(m.group(3)))
                continue

            # ---- Execution timing (marks logical end of step) --------------
            m = RE_EXEC_TIME.match(line)
            if m:
                cur_exec = (float(m.group(1)), float(m.group(2)))
                continue

    # Flush the last step (log may end mid-write without a trailing Time= line)
    if in_step and cur_time is not None:
        flush()

    return records, fields_order

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_FMT = '{:.10e}'
_NA  = 'NaN'

def _ff(v):
    """Format a float, or 'NaN' for None."""
    return _FMT.format(v) if v is not None else _NA

def _fi(v):
    """Format an int, or 'NaN' for None."""
    return str(v) if v is not None else _NA

# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def write_courant(records, out_dir, prefix=''):
    path = os.path.join(out_dir, f'{prefix}courant.txt')
    with open(path, 'w') as fh:
        fh.write('# Time                    deltaT                    Co_mean                   Co_max\n')
        for r in records:
            fh.write('  '.join([
                _ff(r['time']),
                _ff(r['delta_t']),
                _ff(r['co_mean']),
                _ff(r['co_max']),
            ]) + '\n')
    return path


def write_continuity(records, out_dir, prefix=''):
    path = os.path.join(out_dir, f'{prefix}continuity.txt')
    with open(path, 'w') as fh:
        fh.write('# Time                    cont_sum_local            cont_global               cont_cumulative\n')
        for r in records:
            fh.write('  '.join([
                _ff(r['time']),
                _ff(r['cont_sum_local']),
                _ff(r['cont_global']),
                _ff(r['cont_cumulative']),
            ]) + '\n')
    return path


def write_timing(records, out_dir, prefix=''):
    path = os.path.join(out_dir, f'{prefix}timing.txt')
    with open(path, 'w') as fh:
        fh.write('# Time                    ExecutionTime             ClockTime\n')
        for r in records:
            fh.write('  '.join([
                _ff(r['time']),
                _ff(r['exec_time']),
                _ff(r['clock_time']),
            ]) + '\n')
    return path


def write_residuals(records, fields_order, out_dir, prefix=''):
    """
    Write one file per field.

    Columns: Time  InitResid  FinalResid  nIter

    InitResid  — first initial residual for this field this step
    FinalResid — last  final residual for this field this step
    nIter      — total solver iterations this step (summed across correctors)
    """
    written = []
    for field in fields_order:
        path = os.path.join(out_dir, f'{prefix}residuals_{field}.txt')
        with open(path, 'w') as fh:
            fh.write(f'# Field: {field}\n')
            fh.write('# Time                    InitResid                 FinalResid                nIter\n')
            for r in records:
                fd = r['fields'].get(field)
                if fd is not None:
                    row = '  '.join([
                        _ff(r['time']),
                        _ff(fd['init']),
                        _ff(fd['final']),
                        _fi(fd['iters']),
                    ])
                else:
                    # Field not solved this step (can happen with adaptive meshes
                    # or conditional solver activation)
                    row = '  '.join([_ff(r['time']), _NA, _NA, _NA])
                fh.write(row + '\n')
        written.append(path)
    return written

# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def _minmax(vals):
    vs = [v for v in vals if v is not None]
    return (min(vs), max(vs)) if vs else (None, None)


def print_summary(records, fields_order):
    times = [r['time'] for r in records]
    print('\n--- Parse summary ---')
    print(f'  Time steps:    {len(records)}')
    if times:
        print(f'  Time range:    {min(times):.6g}  →  {max(times):.6g}')

    lo, hi = _minmax([r['co_max'] for r in records])
    if lo is not None:
        print(f'  Co max:        {lo:.4g}  →  {hi:.4g}')

    lo, hi = _minmax([r['co_mean'] for r in records])
    if lo is not None:
        print(f'  Co mean:       {lo:.4g}  →  {hi:.4g}')

    lo, hi = _minmax([r['delta_t'] for r in records])
    if lo is not None:
        print(f'  deltaT:        {lo:.4g}  →  {hi:.4g}')

    if fields_order:
        print(f'\n  {"Field":<14}  {"InitResid min":>14}  {"InitResid max":>14}')
        print(f'  {"-"*14}  {"-"*14}  {"-"*14}')
        for field in fields_order:
            inits = [r['fields'][field]['init']
                     for r in records if field in r['fields']]
            lo, hi = _minmax(inits)
            if lo is not None:
                print(f'  {field:<14}  {lo:>14.3e}  {hi:>14.3e}')

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog='foamLogParse.py',
        description=(
            'Parse an OpenFOAM log file and write whitespace-separated .txt files '
            'for plotting (Courant numbers, solver residuals, continuity errors, timing).'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        'logfile',
        help='Path to the OpenFOAM log file.',
    )
    p.add_argument(
        '--out', '-o', metavar='DIR',
        help=(
            'Output directory.  '
            'Default: foamLog_<logname>/ next to the log file.'
        ),
    )
    p.add_argument(
        '--prefix', metavar='STR', default='',
        help='String prepended to every output file name (e.g. "run1_").',
    )
    p.add_argument(
        '--no-courant', action='store_true',
        help='Skip writing courant.txt.',
    )
    p.add_argument(
        '--no-residuals', action='store_true',
        help='Skip writing per-field residual files.',
    )
    p.add_argument(
        '--no-continuity', action='store_true',
        help='Skip writing continuity.txt.',
    )
    p.add_argument(
        '--no-timing', action='store_true',
        help='Skip writing timing.txt.',
    )
    p.add_argument(
        '--verbose', '-v', action='store_true',
        help='Print a summary table after parsing.',
    )
    return p


def main():
    args = build_parser().parse_args()

    log_path = os.path.abspath(args.logfile)
    if not os.path.isfile(log_path):
        sys.exit(f'Error: file not found: {log_path}')

    # Output directory
    if args.out:
        out_dir = os.path.abspath(args.out)
    else:
        log_base = os.path.splitext(os.path.basename(log_path))[0]
        out_dir  = os.path.join(os.path.dirname(log_path),
                                f'foamLog_{log_base}')
    os.makedirs(out_dir, exist_ok=True)

    # Parse
    print(f'Parsing: {log_path}')
    records, fields_order = parse_log(log_path)
    print(f'  {len(records)} time step(s) found.')
    if fields_order:
        print(f'  Fields : {", ".join(fields_order)}')

    if not records:
        sys.exit(
            'No time-step records found.\n'
            'Check that the file is an OpenFOAM log with a running time loop.'
        )

    # Write outputs
    written  = []
    prefix   = args.prefix

    if not args.no_courant:
        written.append(write_courant(records, out_dir, prefix))

    if not args.no_continuity:
        written.append(write_continuity(records, out_dir, prefix))

    if not args.no_timing:
        written.append(write_timing(records, out_dir, prefix))

    if not args.no_residuals:
        written.extend(write_residuals(records, fields_order, out_dir, prefix))

    print(f'\nOutput directory: {out_dir}')
    for p in written:
        print(f'  {os.path.basename(p)}')

    if args.verbose:
        print_summary(records, fields_order)


if __name__ == '__main__':
    main()
