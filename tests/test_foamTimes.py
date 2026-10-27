import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import foamTimes  # noqa: E402

SCRIPT = str(REPO / 'foamTimes.py')


def make_case(root, n_procs, times, missing=None):
    """Create processorN/<t>/ dirs; missing maps a time to processor indices lacking it."""
    missing = missing or {}
    procs = []
    for i in range(n_procs):
        proc = root / 'processor{}'.format(i)
        proc.mkdir()
        for t in times:
            if i not in missing.get(t, ()):
                (proc / t).mkdir()
        procs.append(proc)
    return procs


def make_root_case(root, times):
    for name in ['constant', 'system'] + list(times):
        (root / name).mkdir()


def times_on_disk(proc):
    return sorted((p.name for p in proc.iterdir() if foamTimes.is_time_dir(p.name)), key=float)


class CliTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT] + list(args) + [str(self.root)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        )


class SplitKeepLastTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def split(self, procs, n):
        times = foamTimes.get_times_union(procs)
        return foamTimes.split_keep_last(times, procs, n)

    def test_keeps_newest_n(self):
        procs = make_case(self.root, 2, ['1', '2', '3', '4'])
        self.assertEqual(self.split(procs, 2), (['3', '4'], ['1', '2']))

    def test_n_at_least_count_deletes_nothing(self):
        procs = make_case(self.root, 2, ['1', '2', '3'])
        self.assertEqual(self.split(procs, 3), (['1', '2', '3'], []))
        self.assertEqual(self.split(procs, 5), (['1', '2', '3'], []))

    def test_partial_newest_kept_but_not_counted(self):
        procs = make_case(self.root, 3, ['1', '2', '3', '4'], missing={'4': [2]})
        self.assertEqual(self.split(procs, 2), (['2', '3', '4'], ['1']))

    def test_partial_older_than_cutoff_deleted(self):
        procs = make_case(self.root, 3, ['1', '2', '3', '4'], missing={'2': [0]})
        self.assertEqual(self.split(procs, 2), (['3', '4'], ['1', '2']))

    def test_too_few_complete_deletes_nothing(self):
        procs = make_case(self.root, 2, ['1', '2', '3'], missing={'2': [1], '3': [0]})
        self.assertEqual(self.split(procs, 2), (['1', '2', '3'], []))

    def test_numeric_ordering(self):
        procs = make_case(self.root, 2, ['1e-3', '9', '10', '0.5'])
        self.assertEqual(self.split(procs, 2), (['9', '10'], ['1e-3', '0.5']))


class KeepLastCliTest(CliTestCase):

    def test_rm_removes_old_times_from_every_processor(self):
        procs = make_case(self.root, 3, ['0', '1', '2', '3', '4'], missing={'4': [1]})
        result = self.run_cli('--keep-last', '2', '--rm')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(times_on_disk(procs[0]), ['0', '2', '3', '4'])
        self.assertEqual(times_on_disk(procs[1]), ['0', '2', '3'])
        self.assertEqual(times_on_disk(procs[2]), ['0', '2', '3', '4'])

    def test_listing_shows_keep_and_delete(self):
        make_case(self.root, 2, ['1', '2', '3'], missing={'3': [0]})
        result = self.run_cli('--keep-last', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual([line.split() for line in lines],
                         [['1', 'delete'], ['2', 'keep'], ['3', 'keep', '(partial)']])
        self.assertEqual(lines, [line.rstrip() for line in lines])

    def test_dry_run_leaves_tree_untouched(self):
        procs = make_case(self.root, 2, ['1', '2', '3'])
        result = self.run_cli('--keep-last', '1', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        for proc in procs:
            self.assertEqual(times_on_disk(proc), ['1', '2', '3'])

    def test_with_zero_counts_and_deletes_zero(self):
        procs = make_case(self.root, 2, ['0', '1', '2'])
        result = self.run_cli('--keep-last', '1', '--rm', '--with-zero')
        self.assertEqual(result.returncode, 0, result.stderr)
        for proc in procs:
            self.assertEqual(times_on_disk(proc), ['2'])

    def test_rejects_non_positive_n(self):
        make_case(self.root, 1, ['1', '2'])
        for bad in ('0', '-1', 'x'):
            self.assertNotEqual(self.run_cli('--keep-last', bad, '--rm').returncode, 0)
        self.assertEqual(times_on_disk(self.root / 'processor0'), ['1', '2'])

    def test_rejects_combination_with_all(self):
        make_case(self.root, 1, ['1', '2'])
        self.assertNotEqual(self.run_cli('--keep-last', '1', '--all', '--rm').returncode, 0)
        self.assertEqual(times_on_disk(self.root / 'processor0'), ['1', '2'])


class RootCliTest(CliTestCase):

    def test_undecomposed_lists_root_times(self):
        make_root_case(self.root, ['0', '10', '2', '1e-3'])
        result = self.run_cli('--all')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ['1e-3', '2', '10'])

    def test_undecomposed_keep_last_rm(self):
        make_root_case(self.root, ['0', '1', '2', '3', '4'])
        result = self.run_cli('--keep-last', '2', '--rm')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(times_on_disk(self.root), ['0', '3', '4'])
        self.assertTrue((self.root / 'constant').is_dir())
        self.assertTrue((self.root / 'system').is_dir())

    def test_undecomposed_verbose_has_no_processor_breakdown(self):
        make_root_case(self.root, ['1', '2'])
        result = self.run_cli('--all', '-v')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ['1', '2'])
        self.assertIn('case root', result.stderr)

    def test_decomposed_ignores_root_times_by_default(self):
        procs = make_case(self.root, 2, ['1', '2'])
        make_root_case(self.root, ['0', '2', '4'])
        result = self.run_cli('--all', '--rm')
        self.assertEqual(result.returncode, 0, result.stderr)
        for proc in procs:
            self.assertEqual(times_on_disk(proc), [])
        self.assertEqual(times_on_disk(self.root), ['0', '2', '4'])

    def test_root_flag_acts_only_on_root_times(self):
        procs = make_case(self.root, 2, ['1', '2'])
        make_root_case(self.root, ['0', '2', '4'])
        result = self.run_cli('--root', '--all', '--rm')
        self.assertEqual(result.returncode, 0, result.stderr)
        for proc in procs:
            self.assertEqual(times_on_disk(proc), ['1', '2'])
        self.assertEqual(times_on_disk(self.root), ['0'])


class TimeByValueCliTest(CliTestCase):

    def test_matches_numerically(self):
        procs = make_case(self.root, 2, ['0.001', '8', '10'])
        result = self.run_cli('--time', '8.0', '--rm')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli('--time', '1e-3')
        self.assertEqual(result.stdout.split(), ['0.001'])
        for proc in procs:
            self.assertEqual(times_on_disk(proc), ['0.001', '10'])

    def test_missing_time_is_an_error(self):
        procs = make_case(self.root, 2, ['8', '10'])
        result = self.run_cli('--time', '8.5', '--rm')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No timestep matching '8.5'", result.stderr)
        for proc in procs:
            self.assertEqual(times_on_disk(proc), ['8', '10'])


if __name__ == '__main__':
    unittest.main()
