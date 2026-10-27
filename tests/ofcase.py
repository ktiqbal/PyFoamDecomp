"""
Synthetic OpenFOAM cases and logs for the test suite.

The layouts mirror real OpenFOAM-9 rheoFoam cases: an undecomposed case
(times in the root), a classic decomposed case (processorN/), and a collated
case (processorsN/ or processorsN_a-b/, an empty processor0/, and times
reconstructed into the root). Files are empty; only names and nesting matter.

Randomness comes from case_rng(test_id), seeded from SEED and the test id, so
a test draws the same values whether the suite runs whole or one test alone.
"""

import os
import random
import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / 'src'
# Test modules import ofcase first, so pyfoamdecomp resolves to the working tree.
sys.path.insert(0, str(SRC))

FOAM_TIMES = 'pyfoamdecomp.times'
FOAM_LOG_PARSE = 'pyfoamdecomp.logparse'

SEED = 42
MIN_PROCS, MAX_PROCS = 8, 1024

FIELDS = ('eigVals', 'eigVecs', 'p', 'phi', 'tau', 'theta', 'U')
INITIAL_FIELDS = ('p', 'tau', 'theta', 'U')
POLYMESH = ('boundary', 'cellZones', 'faceZones', 'faces', 'neighbour', 'owner',
            'pointZones', 'points')
PROC_POLYMESH = ('boundary', 'boundaryProcAddressing', 'cellProcAddressing',
                 'faceProcAddressing', 'faces', 'neighbour', 'owner',
                 'pointProcAddressing', 'points')
SYSTEM = ('blockMeshDict', 'controlDict', 'decomposeParDict', 'fvSchemes', 'fvSolution',
          'mirrorMeshDict', 'petscDict', 'sampleDict')
ROOT_FILES = ('Allclean', 'Allrun', 'Cd.txt', 'log.blockMesh', 'log.mirrorMesh', 'log.out',
              'writeData')
DYNAMIC_CODE = ('outputCd/codedFunctionObjectTemplate.C', 'outputCd/codedFunctionObjectTemplate.H',
                'outputCd/Make/files', 'outputCd/Make/options',
                'outputCd/lnInclude/codedFunctionObjectTemplate.C',
                'platforms/linux64GccDPInt32Opt/lib/liboutputCd.so')

Case = namedtuple('Case', 'root procs')


class TempDirTestCase(unittest.TestCase):
    """Gives each test a fresh, resolved temporary directory as self.tmp."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()


def case_rng(test_id):
    return random.Random('{}:{}'.format(SEED, test_id))


def n_procs(rng):
    return rng.randint(MIN_PROCS, MAX_PROCS)


def time_name(t, precision=6):
    """Time dir name as OpenFOAM writes it (C++ general format, timePrecision digits)."""
    return '{:.{}g}'.format(t, precision)


def write_times(start, interval, count, precision=6):
    """Names of count write times, accumulated in floating point like the solver does."""
    names, t = [], float(start)
    for _ in range(count):
        names.append(time_name(t, precision))
        t += interval
    return names


WRITE_TIMES = write_times(2, 2, 7)


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def initial_dir(loc, suffix=''):
    for f in INITIAL_FIELDS:
        touch(loc / '0' / (f + suffix))


def time_dir(loc, t):
    for f in FIELDS:
        touch(loc / t / (f + '.gz'))
    touch(loc / t / 'uniform' / 'time')


def case_root(root, extra_files=()):
    """Root entries shared by every case: 0/, constant/, system/, dynamicCode/, scripts, logs."""
    root.mkdir(parents=True, exist_ok=True)
    initial_dir(root)
    for f in POLYMESH:
        touch(root / 'constant' / 'polyMesh' / (f if f == 'boundary' else f + '.gz'))
    touch(root / 'constant' / 'constitutiveProperties')
    for f in SYSTEM:
        touch(root / 'system' / f)
    for f in DYNAMIC_CODE:
        touch(root / 'dynamicCode' / f)
    for f in ROOT_FILES + tuple(extra_files):
        touch(root / f)


def processor_dir(loc, times, suffix):
    for f in PROC_POLYMESH:
        touch(loc / 'constant' / 'polyMesh' / (f if f == 'boundary' else f + suffix))
    initial_dir(loc, suffix)
    for t in times:
        time_dir(loc, t)


def undecomposed_case(root, times=WRITE_TIMES):
    """Layout of an undecomposed run: write times in the case root."""
    case_root(root)
    for t in times:
        time_dir(root, t)
    return Case(root, [])


def decomposed_case(root, n, times=WRITE_TIMES, missing=None):
    """
    Layout of a classic decomposed run: processor0..n-1/ with compressed fields.
    missing maps a time to the processor indices that lack it (a crashed write).
    """
    missing = missing or {}
    case_root(root, ['log.decomposePar', 'pview.foam'])
    procs = []
    for i in range(n):
        loc = root / 'processor{}'.format(i)
        processor_dir(loc, [t for t in times if i not in missing.get(t, ())], '.gz')
        procs.append(loc)
    return Case(root, procs)


def rank_ranges(n, k):
    """Split processors 0..n-1 into k contiguous (first, last) ranges, as -ioRanks does."""
    bounds = [n * i // k for i in range(k + 1)]
    return [(bounds[i], bounds[i + 1] - 1) for i in range(k)]


def collated_case(root, n, times=WRITE_TIMES, reconstructed=WRITE_TIMES, io_ranks=1, missing=None):
    """
    Layout of a collated run: processors<n>/ (or io_ranks processors<n>_a-b/ dirs)
    with uncompressed 0/ and polyMesh, an empty processor0/, and the times in
    reconstructed written to the root by reconstructPar. missing maps a time to
    the indices of the collated dirs that lack it.
    """
    missing = missing or {}
    case_root(root, ['case.foam', 'log.decomposePar', 'reconstructPar.log'])
    for t in reconstructed:
        time_dir(root, t)
    (root / 'processor0').mkdir()
    if io_ranks == 1:
        names = ['processors{}'.format(n)]
    else:
        names = ['processors{}_{}-{}'.format(n, a, b) for a, b in rank_ranges(n, io_ranks)]
    procs = []
    for i, name in enumerate(names):
        loc = root / name
        processor_dir(loc, [t for t in times if i not in missing.get(t, ())], '')
        procs.append(loc)
    return Case(root, procs)


def times_on_disk(loc):
    """Time dir names in loc, numerically sorted (independent of case.is_time_dir)."""
    names = []
    for p in loc.iterdir():
        try:
            float(p.name)
        except ValueError:
            continue
        names.append(p.name)
    return sorted(names, key=float)


def snapshot(root):
    """Every path under root, relative and '/'-separated."""
    paths = set()
    for dirpath, dirnames, filenames in os.walk(str(root)):
        rel = Path(dirpath).relative_to(root)
        for name in dirnames + filenames:
            paths.add((rel / name).as_posix())
    return paths


def without(paths, removed):
    """paths minus every entry in or below one of the removed dirs."""
    removed = set(removed)

    def gone(p):
        parts = p.split('/')
        return any('/'.join(parts[:i]) in removed for i in range(1, len(parts) + 1))

    return {p for p in paths if not gone(p)}


def run_command(module, *args):
    """Run a pyfoamdecomp command module from the working tree in a subprocess."""
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(SRC), env.get('PYTHONPATH')]))
    return subprocess.run(
        [sys.executable, '-m', module] + [str(a) for a in args],
        capture_output=True, text=True, env=env,
    )


# ---------------------------------------------------------------------------
# Synthetic rheoFoam log
# ---------------------------------------------------------------------------

THETA = ('thetaxx', 'thetaxy', 'thetayy', 'thetazz')
COUPLED = ('p', 'Ux', 'Uy')
LOG_FIELDS = THETA + COUPLED
DELTA_T = 0.02
GARBLED_LINE = b'[17] \xff\xfe\x80 MPI output cut by another rank\n'


def _residual(rng):
    return 10 ** rng.uniform(-16, 0)


def _solve(rng, record, field, line_prefix, zero=False):
    if zero:
        init = final = 0.0
        iters = 0
        text = '0, Final residual = 0, No Iterations 0'
    else:
        init, final, iters = _residual(rng), _residual(rng), rng.randint(1, 20)
        text = '{!r}, Final residual = {!r}, No Iterations {}'.format(init, final, iters)
    rec = record['fields'].get(field)
    if rec is None:
        record['fields'][field] = {'init': init, 'final': final, 'iters': iters}
    else:
        rec['final'] = final
        rec['iters'] += iters
    return '{}{}, Initial residual = {}\n'.format(line_prefix, field, text)


def rheofoam_log(path, rng, nprocs, n_steps, truncate=False, delta_t_lines=False, garbled=False):
    """
    Write a log shaped like a parallel OpenFOAM-9 rheoFoam run and return the
    records parse_log should produce. truncate cuts the last step after its
    solves (a live log); delta_t_lines adds 'deltaT =' lines (adjustTimeStep);
    garbled inserts a line of invalid UTF-8 between steps.
    """
    out = [
        '/*---------------------------------------------------------------------------*\\\n',
        '  =========                 |\n',
        '  \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox\n',
        '   \\\\  /    A nd           | Version:  9\n',
        '\\*---------------------------------------------------------------------------*/\n',
        'Build  : 9-d87800e1bde0\n',
        'Exec   : rheoFoam -parallel\n',
        'Date   : Oct 27 2026\n',
        'Time   : 16:07:19\n',
        'Host   : "node001"\n',
        'PID    : 1234436\n',
        'I/O    : uncollated\n',
        'Case   : /scratch/case\n',
        'nProcs : {}\n'.format(nprocs),
        'Slaves : \n{}\n(\n'.format(nprocs - 1),
    ]
    out += ['"node{:03d}.{}"\n'.format(i // 64, 1234437 + i) for i in range(nprocs - 1)]
    out += [')\n', '\n', 'Create time\n', '\n', 'Create mesh for time = 0\n', '\n',
            'Courant Number mean: 0 max: 0\n', '\n', 'Starting time loop\n', '\n']
    chunks = [''.join(out).encode()]

    records, t, clock = [], 0.0, 8
    for step in range(n_steps):
        t += DELTA_T
        name = time_name(t, 10)
        record = {'time': float(name), 'delta_t': None,
                  'co_mean': rng.uniform(0, 0.1), 'co_max': rng.uniform(0.1, 3),
                  'cont_sum_local': None, 'cont_global': None, 'cont_cumulative': None,
                  'exec_time': None, 'clock_time': None, 'fields': {}}
        lines = ['Courant Number mean: {!r} max: {!r}\n'.format(record['co_mean'], record['co_max'])]
        if delta_t_lines:
            record['delta_t'] = DELTA_T
            lines.append('deltaT = {!r}\n'.format(DELTA_T))
        lines += ['Time = {}\n'.format(name), '\n', 'Inner iteration:  0\n', '\n']
        for field in THETA:
            lines.append(_solve(rng, record, field, 'DILUPBiCG:  Solving for ', zero=field == 'thetazz'))
        n_inner = rng.choice((1, 1, 2, 3))
        for inner in range(n_inner):
            if inner:
                lines += ['\n', 'Inner iteration:  {}\n'.format(inner), '\n']
            lines.append('Coupled system: p, U\n')
            for field in COUPLED:
                lines.append(_solve(rng, record, field, 'Petsc:preonly:lu: Solving coupled for '))
        records.append(record)
        if truncate and step == n_steps - 1:
            chunks.append(''.join(lines).encode())
            break
        cont = (rng.uniform(0, 1e-3), rng.uniform(-1e-15, 1e-15), rng.uniform(-1e-14, 1e-14))
        record['cont_sum_local'], record['cont_global'], record['cont_cumulative'] = cont
        clock += rng.randint(0, 2)
        record['exec_time'], record['clock_time'] = round(1 + step * 0.3, 2), float(clock)
        lines.append('time step continuity errors : sum local = {!r}, global = {!r}, '
                     'cumulative = {!r}\n'.format(*cont))
        lines += ['ExecutionTime = {!r} s  ClockTime = {} s\n'.format(record['exec_time'], clock),
                  '\n']
        chunks.append(''.join(lines).encode())
        if garbled and step == n_steps // 2:
            chunks.append(GARBLED_LINE)
    else:
        chunks.append(b'End\n\nFinalising parallel run\n')

    Path(path).write_bytes(b''.join(chunks))
    return records
