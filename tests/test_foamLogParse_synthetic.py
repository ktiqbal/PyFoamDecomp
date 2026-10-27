import sys
import tempfile
import unittest
from pathlib import Path

import ofcase
from ofcase import FOAM_LOG_PARSE, LOG_FIELDS, REPO

sys.path.insert(0, str(REPO))

import foamLogParse  # noqa: E402


def row(*values):
    return '  '.join('NaN' if v is None else '{:.10e}'.format(v) for v in values)


class SyntheticLogTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.log = self.root / 'log.out'
        self.rng = ofcase.case_rng(self.id())
        self.n = ofcase.n_procs(self.rng)
        self.n_steps = self.rng.randint(20, 200)
        self.msg = 'n_procs={}, n_steps={}, seed {!r}'.format(
            self.n, self.n_steps, '{}:{}'.format(ofcase.SEED, self.id()))

    def tearDown(self):
        self._tmp.cleanup()

    def write_log(self, **kwargs):
        return ofcase.rheofoam_log(self.log, self.rng, self.n, self.n_steps, **kwargs)

    def parse(self):
        return foamLogParse.parse_log(str(self.log))

    def run_cli(self, *args):
        result = ofcase.run_script(FOAM_LOG_PARSE, self.log, '--out', self.root / 'out', *args)
        self.assertEqual(result.returncode, 0, '{}\n{}'.format(result.stderr, self.msg))
        return result

    def output(self, name):
        return (self.root / 'out' / name).read_text().splitlines()

    def test_records_match_generated_values(self):
        expected = self.write_log()
        records, fields = self.parse()
        self.assertEqual(fields, list(LOG_FIELDS), self.msg)
        self.assertEqual(len(records), self.n_steps, self.msg)
        for got, want in zip(records, expected):
            self.assertEqual(got, want, 'time {}: {}'.format(want['time'], self.msg))

    def test_header_time_line_and_pre_loop_courant_ignored(self):
        expected = self.write_log()
        records, _ = self.parse()
        self.assertEqual(records[0]['time'], 0.02, self.msg)
        self.assertEqual(records[0]['co_max'], expected[0]['co_max'], self.msg)

    def test_fixed_time_step_has_no_delta_t(self):
        self.write_log()
        records, _ = self.parse()
        self.assertTrue(all(r['delta_t'] is None for r in records), self.msg)

    def test_delta_t_lines_attach_to_following_step(self):
        expected = self.write_log(delta_t_lines=True)
        records, _ = self.parse()
        self.assertEqual(records, expected, self.msg)
        self.assertTrue(all(r['delta_t'] == ofcase.DELTA_T for r in records), self.msg)

    def test_truncated_live_log_keeps_partial_last_step(self):
        expected = self.write_log(truncate=True)
        records, _ = self.parse()
        self.assertEqual(records, expected, self.msg)
        last = records[-1]
        self.assertIsNone(last['exec_time'], self.msg)
        self.assertIsNone(last['cont_global'], self.msg)
        self.assertEqual(set(last['fields']), set(LOG_FIELDS), self.msg)

    def test_invalid_utf8_line_does_not_break_parsing(self):
        expected = self.write_log(garbled=True)
        self.assertIn(ofcase.GARBLED_LINE, self.log.read_bytes())
        records, _ = self.parse()
        self.assertEqual(records, expected, self.msg)

    def test_cli_tables_have_exact_format(self):
        expected = self.write_log(truncate=True)
        result = self.run_cli()
        self.assertIn('{} time step(s) found.'.format(self.n_steps), result.stdout)

        courant = self.output('courant.txt')
        self.assertTrue(courant[0].startswith('# Time'))
        self.assertEqual(courant[1:], [row(r['time'], None, r['co_mean'], r['co_max'])
                                       for r in expected], self.msg)
        timing = self.output('timing.txt')
        self.assertEqual(timing[1:], [row(r['time'], r['exec_time'], r['clock_time'])
                                      for r in expected], self.msg)
        self.assertTrue(timing[-1].endswith('NaN  NaN'), self.msg)
        continuity = self.output('continuity.txt')
        self.assertEqual(continuity[1:], [
            row(r['time'], r['cont_sum_local'], r['cont_global'], r['cont_cumulative'])
            for r in expected], self.msg)

        for field in LOG_FIELDS:
            lines = self.output('residuals_{}.txt'.format(field))
            self.assertEqual(lines[0], '# Field: {}'.format(field))
            self.assertEqual(lines[2:], [
                '{}  {}'.format(row(r['time'], r['fields'][field]['init'], r['fields'][field]['final']),
                                r['fields'][field]['iters'])
                for r in expected], '{}: {}'.format(field, self.msg))
        self.assertEqual(self.output('residuals_thetazz.txt')[2].split()[1:],
                         ['0.0000000000e+00', '0.0000000000e+00', '0'])

    def test_inner_iterations_merge_first_init_last_final_summed_iters(self):
        self.write_log()
        steps = self.log.read_text().split('\nTime = ')[1:]
        records, _ = self.parse()
        multi = [i for i, step in enumerate(steps) if 'Inner iteration:  1' in step]
        self.assertTrue(multi, self.msg)
        for i in multi:
            solves = [line.split(', ') for line in steps[i].splitlines()
                      if line.startswith('Petsc:preonly:lu: Solving coupled for p,')]
            self.assertGreater(len(solves), 1)
            self.assertEqual(records[i]['fields']['p'], {
                'init': float(solves[0][1].split('= ')[1]),
                'final': float(solves[-1][2].split('= ')[1]),
                'iters': sum(int(s[3].split()[-1]) for s in solves),
            }, self.msg)

if __name__ == '__main__':
    unittest.main()
