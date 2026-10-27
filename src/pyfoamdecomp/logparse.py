"""
foamLogParse -- Parse OpenFOAM log files for post-processing and plotting.

Extracts per-timestep data and writes whitespace-separated .txt files that
load cleanly with numpy.loadtxt, pandas.read_csv(sep='\\s+'), or gnuplot.

Extracted quantities
--------------------
  courant.txt           -- simulation time, deltaT, mean and max Courant numbers
  continuity.txt        -- continuity error: sum-local, global, cumulative
  timing.txt            -- ExecutionTime and ClockTime per step
  residuals_<field>.txt -- per-field: first initial residual, last final
                           residual, total solver iterations

When a field is solved multiple times per step (e.g. pressure in PISO/PIMPLE
corrector loops) the *first* initial residual and the *last* final residual are
recorded, and iteration counts are summed.

Usage
-----
    foamLogParse <logfile>
    foamLogParse <logfile> --out results/
    foamLogParse <logfile> --prefix run1_ --verbose
    foamLogParse <logfile> --no-continuity --no-timing
"""

import argparse
import re
from pathlib import Path

from pyfoamdecomp.cli import fail

# ---------------------------------------------------------------------------
# Regular expressions
# ---------------------------------------------------------------------------

# "Courant Number mean: 0.000181 max: 0.009999"; searched rather than matched
# so that "Interface Courant Number mean: ..." is picked up too.
RE_COURANT = re.compile(r'Courant Number mean:\s*(\S+)\s+max:\s*(\S+)')

# "deltaT = 8.594e-05"
RE_DELTA_T = re.compile(r'^deltaT\s*=\s*(\S+)')

# "Time = 6.000089317481708"
RE_TIME = re.compile(r'^Time\s*=\s*(\S+)')

# "DILUPBiCG:  Solving for Ux, Initial residual = 6.95e-07, Final residual = 8.42e-09, No Iterations 7"
# "Petsc:preonly:lu: Solving coupled for p, Initial residual = ..."  (rheoTool coupled solver)
RE_SOLVER = re.compile(
    r'^\S+:\s+Solving (?:coupled )?for (\S+),\s+'
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
RE_EXEC_TIME = re.compile(r'^ExecutionTime\s*=\s*(\S+)\s+s\s+ClockTime\s*=\s*(\S+)\s+s')

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_log(path):
    """
    Single-pass streaming parse of an OpenFOAM log file.

    Returns (records, fields_order):
      records       one dict per time step (the last may be in progress) with keys
                    'time', 'delta_t', 'co_mean', 'co_max', 'exec_time', 'clock_time',
                    'cont_sum_local', 'cont_global', 'cont_cumulative', and
                    'fields': {field: {'init': float, 'final': float, 'iters': int}}
      fields_order  every solved field name, in order of first appearance
    """
    records = []
    fields_order = []
    # Courant and deltaT lines come BEFORE their "Time =" line.
    pending = {'co_mean': None, 'co_max': None, 'delta_t': None}
    cur = None

    with open(path, errors='replace') as fh:
        for line in fh:
            m = RE_COURANT.search(line)
            if m:
                pending['co_mean'], pending['co_max'] = float(m.group(1)), float(m.group(2))
                continue

            m = RE_DELTA_T.match(line)
            if m:
                pending['delta_t'] = float(m.group(1))
                continue

            m = RE_TIME.match(line)
            if m:
                cur = {
                    'time': float(m.group(1)),
                    **pending,
                    'cont_sum_local': None,
                    'cont_global': None,
                    'cont_cumulative': None,
                    'exec_time': None,
                    'clock_time': None,
                    'fields': {},
                }
                records.append(cur)
                pending = dict.fromkeys(pending)
                continue

            if cur is None:
                continue

            m = RE_SOLVER.match(line)
            if m:
                field = m.group(1)
                init_res, final_res, n_iter = float(m.group(2)), float(m.group(3)), int(m.group(4))
                if field not in fields_order:
                    fields_order.append(field)
                solve = cur['fields'].get(field)
                if solve is None:
                    cur['fields'][field] = {'init': init_res, 'final': final_res, 'iters': n_iter}
                else:
                    solve['final'] = final_res
                    solve['iters'] += n_iter
                continue

            m = RE_CONTINUITY.match(line)
            if m:
                cur['cont_sum_local'], cur['cont_global'], cur['cont_cumulative'] = (
                    float(v) for v in m.groups())
                continue

            m = RE_EXEC_TIME.match(line)
            if m:
                cur['exec_time'], cur['clock_time'] = float(m.group(1)), float(m.group(2))

    return records, fields_order

# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

NA = 'NaN'


def fmt_float(v):
    return NA if v is None else f'{v:.10e}'


# (name, header, record keys); each is written to <prefix><name>.txt
TABLES = (
    ('courant',
     '# Time                    deltaT                    Co_mean                   Co_max',
     ('time', 'delta_t', 'co_mean', 'co_max')),
    ('continuity',
     '# Time                    cont_sum_local            cont_global               cont_cumulative',
     ('time', 'cont_sum_local', 'cont_global', 'cont_cumulative')),
    ('timing',
     '# Time                    ExecutionTime             ClockTime',
     ('time', 'exec_time', 'clock_time')),
)


def write_columns(records, path, header, keys):
    with open(path, 'w') as fh:
        fh.write(header + '\n')
        for r in records:
            fh.write('  '.join(fmt_float(r[k]) for k in keys) + '\n')
    return path


def write_residuals(records, fields_order, out_dir, prefix=''):
    """
    Write residuals_<field>.txt per field with columns Time InitResid FinalResid nIter,
    and NaN for steps in which the field was not solved.
    """
    written = []
    for field in fields_order:
        path = out_dir / f'{prefix}residuals_{field}.txt'
        with open(path, 'w') as fh:
            fh.write(f'# Field: {field}\n')
            fh.write('# Time                    InitResid                 FinalResid                nIter\n')
            for r in records:
                solve = r['fields'].get(field)
                if solve is None:
                    values = [NA, NA, NA]
                else:
                    values = [fmt_float(solve['init']), fmt_float(solve['final']), str(solve['iters'])]
                fh.write('  '.join([fmt_float(r['time'])] + values) + '\n')
        written.append(path)
    return written

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _minmax(vals):
    vs = [v for v in vals if v is not None]
    return (min(vs), max(vs)) if vs else (None, None)


def print_summary(records, fields_order):
    times = [r['time'] for r in records]
    print('\n--- Parse summary ---')
    print(f'  Time steps:    {len(records)}')
    if times:
        print(f'  Time range:    {min(times):.6g}  ->  {max(times):.6g}')

    for label, key in (('Co max', 'co_max'), ('Co mean', 'co_mean'), ('deltaT', 'delta_t')):
        lo, hi = _minmax([r[key] for r in records])
        if lo is not None:
            print(f'  {label + ":":<15}{lo:.4g}  ->  {hi:.4g}')

    if fields_order:
        print(f'\n  {"Field":<14}  {"InitResid min":>14}  {"InitResid max":>14}')
        print(f'  {"-"*14}  {"-"*14}  {"-"*14}')
        for field in fields_order:
            lo, hi = _minmax([r['fields'][field]['init'] for r in records if field in r['fields']])
            if lo is not None:
                print(f'  {field:<14}  {lo:>14.3e}  {hi:>14.3e}')

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog='foamLogParse',
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
        help='Output directory.  Default: foamLog_<logname>/ next to the log file.',
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


def main(argv=None):
    args = build_parser().parse_args(argv)

    log_path = Path(args.logfile).resolve()
    if not log_path.is_file():
        fail(f'file not found: {log_path}')

    if args.out:
        out_dir = Path(args.out).resolve()
    else:
        out_dir = log_path.parent / f'foamLog_{log_path.stem}'
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f'Parsing: {log_path}')
    records, fields_order = parse_log(log_path)
    print(f'  {len(records)} time step(s) found.')
    if fields_order:
        print(f'  Fields : {", ".join(fields_order)}')

    if not records:
        fail('No time-step records found.\n'
             'Check that the file is an OpenFOAM log with a running time loop.')

    written = []
    for name, header, keys in TABLES:
        if not getattr(args, f'no_{name}'):
            written.append(write_columns(records, out_dir / f'{args.prefix}{name}.txt', header, keys))
    if not args.no_residuals:
        written.extend(write_residuals(records, fields_order, out_dir, args.prefix))

    print(f'\nOutput directory: {out_dir}')
    for path in written:
        print(f'  {path.name}')

    if args.verbose:
        print_summary(records, fields_order)


if __name__ == '__main__':
    main()
