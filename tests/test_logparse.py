import unittest

from ofcase import FOAM_LOG_PARSE, TempDirTestCase, run_command
from pyfoamdecomp import logparse

COUPLED_LOG = """\
Courant Number mean: 0.0001 max: 0.008
Starting time loop

Courant Number mean: 0.0001 max: 0.008
Time = 0.02

Inner iteration:  0

DILUPBiCG:  Solving for thetaxx, Initial residual = 1, Final residual = 2.9e-16, No Iterations 1
Coupled system: p, U
Petsc:preonly:lu: Solving coupled for p, Initial residual = 1, Final residual = 1.2e-16, No Iterations 1
Petsc:preonly:lu: Solving coupled for Ux, Initial residual = 1, Final residual = 1.1e-14, No Iterations 1
Petsc:preonly:lu: Solving coupled for Uy, Initial residual = 1, Final residual = 0.99, No Iterations 1
time step continuity errors : sum local = 0.0006, global = 3.9e-16, cumulative = 3.9e-16
ExecutionTime = 1.31 s  ClockTime = 8 s

Courant Number mean: 0.066 max: 2.8
Time = 0.04

Inner iteration:  0

DILUPBiCG:  Solving for thetaxx, Initial residual = 0.52, Final residual = 4.7e-11, No Iterations 7
Coupled system: p, U
Petsc:preonly:lu: Solving coupled for p, Initial residual = 8.8e-06, Final residual = 1.3e-16, No Iterations 1
Petsc:preonly:lu: Solving coupled for Ux, Initial residual = 0.47, Final residual = 7.7e-15, No Iterations 1
Petsc:preonly:lu: Solving coupled for Uy, Initial residual = 0.5, Final residual = 3.8e-13, No Iterations 1

Inner iteration:  1

Coupled system: p, U
Petsc:preonly:lu: Solving coupled for p, Initial residual = 2e-07, Final residual = 1e-17, No Iterations 2
Petsc:preonly:lu: Solving coupled for Ux, Initial residual = 0.01, Final residual = 5e-16, No Iterations 1
Petsc:preonly:lu: Solving coupled for Uy, Initial residual = 0.02, Final residual = 6e-16, No Iterations 1
time step continuity errors : sum local = 0.0005, global = 4.6e-17, cumulative = 4.3e-16
ExecutionTime = 1.92 s  ClockTime = 9 s
"""


class LogTestCase(TempDirTestCase):

    def setUp(self):
        super().setUp()
        self.root = self.tmp
        self.log = self.root / 'log.out'

    def run_cli(self, *args):
        return run_command(FOAM_LOG_PARSE, self.log, '--out', self.root / 'out', *args)


class CoupledResidualsTest(LogTestCase):

    def setUp(self):
        super().setUp()
        self.log.write_text(COUPLED_LOG)

    def test_coupled_fields_discovered(self):
        records, fields = logparse.parse_log(str(self.log))
        self.assertEqual(fields, ['thetaxx', 'p', 'Ux', 'Uy'])
        self.assertEqual([r['time'] for r in records], [0.02, 0.04])
        self.assertEqual(records[0]['fields']['Uy'], {'init': 1.0, 'final': 0.99, 'iters': 1})
        self.assertEqual(records[1]['fields']['thetaxx'], {'init': 0.52, 'final': 4.7e-11, 'iters': 7})

    def test_coupled_inner_iterations_merged(self):
        records, _ = logparse.parse_log(str(self.log))
        self.assertEqual(records[1]['fields']['p'], {'init': 8.8e-06, 'final': 1e-17, 'iters': 3})
        self.assertEqual(records[1]['fields']['Ux'], {'init': 0.47, 'final': 5e-16, 'iters': 2})

    def test_cli_writes_coupled_residual_files(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = (self.root / 'out' / 'residuals_p.txt').read_text().splitlines()
        data = [[float(v) for v in row.split()] for row in rows if not row.startswith('#')]
        self.assertEqual(data, [[0.02, 1.0, 1.2e-16, 1.0], [0.04, 8.8e-06, 1e-17, 3.0]])
        for field in ('Ux', 'Uy', 'thetaxx'):
            self.assertTrue((self.root / 'out' / 'residuals_{}.txt'.format(field)).is_file())

    def test_verbose_summary_is_ascii(self):
        result = self.run_cli('-v')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('->', result.stdout)
        self.assertTrue(all(ord(c) < 128 for c in result.stdout))


class CliErrorTest(LogTestCase):

    def test_missing_logfile_is_an_error(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('ERROR: file not found: {}'.format(self.log), result.stderr)
        self.assertFalse((self.root / 'out').exists())

    def test_log_without_time_steps_is_an_error(self):
        self.log.write_text('Courant Number mean: 0 max: 0\nStarting time loop\n')
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn('ERROR: No time-step records found.', result.stderr)
        self.assertEqual(list((self.root / 'out').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
