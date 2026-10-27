import tempfile
import unittest
from pathlib import Path

from ofcase import FOAM_TIMES, run_command, times_on_disk
from pyfoamdecomp import times as foamTimes


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


def make_collated(root, name, times):
    loc = root / name
    for sub in ['constant'] + list(times):
        (loc / sub).mkdir(parents=True)
    return loc


class CliTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        return run_command(FOAM_TIMES, *(args + (self.root,)))


class SplitKeepLastTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def split(self, procs, n):
        times = foamTimes.get_times_union([procs])
        return foamTimes.split_keep_last(times, [procs], n)

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


class ProcessorOrderTest(CliTestCase):

    def test_missing_processors_listed_in_numeric_order(self):
        make_case(self.root, 12, ['1', '2'], missing={'2': [10, 2]})
        result = self.run_cli('--all', '-v')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1],
                         '2  10/12 processor*  [MISSING: processor2, processor10]')

    def test_io_rank_dirs_sorted_by_first_rank(self):
        for name in ('processors12_10-11', 'processors12_0-1', 'processors12_2-9'):
            make_collated(self.root, name, ['1'])
        families = foamTimes.find_processor_families(self.root)
        self.assertEqual([p.name for p in families['processors12']],
                         ['processors12_0-1', 'processors12_2-9', 'processors12_10-11'])


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

    def test_decomposed_rm_without_side_is_an_error(self):
        procs = make_case(self.root, 2, ['1', '2'])
        make_root_case(self.root, ['0', '2', '4'])
        for args in (['--all', '--rm'], ['--rm'], ['--keep-last', '1', '-n']):
            result = self.run_cli(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('--root, --decomposed, or both', result.stderr)
        for proc in procs:
            self.assertEqual(times_on_disk(proc), ['1', '2'])
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


class CollatedCliTest(CliTestCase):

    def test_empty_processor0_is_ignored(self):
        (self.root / 'processor0').mkdir()
        coll = make_collated(self.root, 'processors4', ['0', '2', '4', '6'])
        result = self.run_cli('--keep-last', '2', '--rm')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(times_on_disk(coll), ['0', '4', '6'])
        self.assertTrue((coll / 'constant').is_dir())

    def test_io_rank_split_dirs_form_one_side(self):
        a = make_collated(self.root, 'processors4_0-1', ['1', '2', '3'])
        b = make_collated(self.root, 'processors4_2-3', ['1', '2'])
        result = self.run_cli('--keep-last', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split() for line in result.stdout.splitlines()],
                         [['1', 'delete'], ['2', 'keep'], ['3', 'keep', '(partial)']])
        result = self.run_cli('--all', '-v')
        self.assertIn('1/2 processors4_*  [MISSING: processors4_2-3]', result.stdout)
        self.assertEqual(times_on_disk(a), ['1', '2', '3'])
        self.assertEqual(times_on_disk(b), ['1', '2'])

    def test_several_layouts_with_times_is_an_error(self):
        procs = make_case(self.root, 2, ['1', '2'])
        coll = make_collated(self.root, 'processors2', ['1', '2'])
        result = self.run_cli('--all', '--rm')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('processor*, processors2', result.stderr)
        self.assertEqual(times_on_disk(procs[0]), ['1', '2'])
        self.assertEqual(times_on_disk(coll), ['1', '2'])
        self.assertEqual(self.run_cli('--root', '--all').returncode, 0)


class SidesCliTest(CliTestCase):

    def setUp(self):
        super().setUp()
        (self.root / 'processor0').mkdir()
        self.coll = make_collated(self.root, 'processors4', ['0', '2', '4', '6', '8'])
        make_root_case(self.root, ['0', '2', '4', '6'])

    def test_listing_is_union_of_both_sides(self):
        result = self.run_cli('--all')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ['2', '4', '6', '8'])

    def test_verbose_shows_a_column_per_side(self):
        result = self.run_cli('--all', '-v')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], '2  root  processors4')
        self.assertEqual(lines[-1], '8        processors4')
        self.assertIn('root, processors4', result.stderr)

    def test_rm_without_side_leaves_tree_untouched(self):
        result = self.run_cli('--all', '--rm')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--root, --decomposed, or both', result.stderr)
        self.assertEqual(times_on_disk(self.coll), ['0', '2', '4', '6', '8'])
        self.assertEqual(times_on_disk(self.root), ['0', '2', '4', '6'])

    def test_decomposed_rm_only_touches_processor_dirs(self):
        result = self.run_cli('--decomposed', '--keep-last', '2', '--rm')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(times_on_disk(self.coll), ['0', '6', '8'])
        self.assertEqual(times_on_disk(self.root), ['0', '2', '4', '6'])

    def test_root_rm_only_touches_case_root(self):
        result = self.run_cli('--root', '--all', '--rm')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(times_on_disk(self.root), ['0'])
        self.assertEqual(times_on_disk(self.coll), ['0', '2', '4', '6', '8'])

    def test_both_flags_delete_from_both_sides(self):
        result = self.run_cli('--root', '--decomposed', '--time', '4', '--rm')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(times_on_disk(self.root), ['0', '2', '6'])
        self.assertEqual(times_on_disk(self.coll), ['0', '2', '6', '8'])

    def test_root_only_time_counts_as_complete(self):
        (self.coll / '6').rmdir()
        sides = [[self.root], [self.coll]]
        times = foamTimes.get_times_union(sides)
        self.assertEqual(foamTimes.split_keep_last(times, sides, 2), (['6', '8'], ['2', '4']))

    def test_decomposed_on_undecomposed_case_is_an_error(self):
        self._tmp.cleanup()
        self.root.mkdir()
        make_root_case(self.root, ['1', '2'])
        result = self.run_cli('--decomposed', '--all')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No processor* directories with timesteps', result.stderr)


class SideAmbiguityCliTest(CliTestCase):

    def test_root_initial_conditions_alone_do_not_make_rm_ambiguous(self):
        procs = make_case(self.root, 2, ['0', '1', '2'])
        make_root_case(self.root, ['0'])
        result = self.run_cli('--all', '--rm')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(times_on_disk(procs[0]), ['0'])
        result = self.run_cli('--all', '--rm', '--with-zero')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(times_on_disk(self.root), ['0'])

    def test_empty_processor_dirs_never_fall_back_to_root(self):
        make_collated(self.root, 'processors4', [])
        make_root_case(self.root, ['0', '1', '2'])
        result = self.run_cli('--all', '--rm')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pass --root', result.stderr)
        self.assertEqual(times_on_disk(self.root), ['0', '1', '2'])
        result = self.run_cli('--all')
        self.assertEqual(result.stdout.split(), ['1', '2'])


if __name__ == '__main__':
    unittest.main()
