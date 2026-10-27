import unittest

import ofcase
from ofcase import FOAM_TIMES, WRITE_TIMES, TempDirTestCase, snapshot, times_on_disk, without


class SyntheticCaseTest(TempDirTestCase):

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'case'
        self.rng = ofcase.case_rng(self.id())
        self.n = ofcase.n_procs(self.rng)
        self.msg = 'n_procs={}, seed {!r}'.format(self.n, '{}:{}'.format(ofcase.SEED, self.id()))

    def run_cli(self, *args):
        return ofcase.run_command(FOAM_TIMES, *(args + (self.root,)))

    def assertOk(self, result):
        self.assertEqual(result.returncode, 0, '{}\n{}'.format(result.stderr, self.msg))

    def assertFails(self, result, text):
        self.assertNotEqual(result.returncode, 0, self.msg)
        self.assertIn(text, result.stderr, self.msg)

    def assertTimes(self, loc, expected):
        self.assertEqual(times_on_disk(loc), expected, '{}: {}'.format(loc.name, self.msg))

    def assertRemovedOnly(self, before, removed):
        """Only the removed dirs (and their contents) are gone; nothing else changed."""
        self.assertEqual(snapshot(self.root), without(before, removed), self.msg)


class UndecomposedTest(SyntheticCaseTest):

    def setUp(self):
        super().setUp()
        ofcase.undecomposed_case(self.root)

    def test_lists_write_times_numerically(self):
        result = self.run_cli('--all')
        self.assertOk(result)
        self.assertEqual(result.stdout.split(), WRITE_TIMES)
        self.assertEqual(self.run_cli().stdout.split(), ['14'])

    def test_keep_last_rm_leaves_case_files(self):
        before = snapshot(self.root)
        self.assertOk(self.run_cli('--keep-last', '2', '--rm'))
        self.assertTimes(self.root, ['0', '12', '14'])
        self.assertRemovedOnly(before, ['2', '4', '6', '8', '10'])

    def test_decomposed_flag_is_an_error(self):
        self.assertFails(self.run_cli('--decomposed', '--all'),
                         'No processor* directories with timesteps')


class TimeNamesTest(SyntheticCaseTest):

    def test_fractional_and_scientific_times(self):
        values = [1e-05, 2e-05, 5e-05, 0.0001, 0.05, 0.1, 0.15, 1, 1.05, 10]
        names = [ofcase.time_name(v) for v in values]
        self.assertEqual(names[:4], ['1e-05', '2e-05', '5e-05', '0.0001'])
        shuffled = list(names)
        self.rng.shuffle(shuffled)
        ofcase.undecomposed_case(self.root, shuffled)

        result = self.run_cli('--all')
        self.assertOk(result)
        self.assertEqual(result.stdout.split(), names)
        self.assertEqual(self.run_cli('--time', '0.00002').stdout.split(), ['2e-05'])
        self.assertOk(self.run_cli('--time', '1.050', '--rm'))
        self.assertOk(self.run_cli('--time', '2E-5', '--rm'))
        self.assertTimes(self.root, ['0'] + [t for t in names if t not in ('1.05', '2e-05')])


class DecomposedTest(SyntheticCaseTest):

    def build(self, missing=None):
        case = ofcase.decomposed_case(self.root, self.n, missing=missing)
        return case.procs

    def partial_latest(self):
        """A crashed final write: more than five random processors lack '14'."""
        lacking = sorted(self.rng.sample(range(self.n), self.rng.randint(6, self.n - 1)))
        return lacking, self.build(missing={'14': lacking})

    def test_lists_union_with_full_presence(self):
        self.build()
        result = self.run_cli('--all', '-v')
        self.assertOk(result)
        self.assertEqual(result.stdout.splitlines(),
                         ['{:<2}  {}/{} processor*'.format(t, self.n, self.n) for t in WRITE_TIMES])
        self.assertIn('processor* ({} dirs)'.format(self.n), result.stderr)

    def test_rm_latest_from_every_processor(self):
        procs = self.build()
        before = snapshot(self.root)
        result = self.run_cli('--rm')
        self.assertOk(result)
        self.assertEqual(result.stdout, 'Deleted time(s): 14\n')
        self.assertRemovedOnly(before, ['processor{}/14'.format(i) for i in range(self.n)])
        for proc in procs:
            self.assertTimes(proc, ['0'] + WRITE_TIMES[:-1])

    def test_crashed_run_verbose_names_missing_processors(self):
        lacking, _ = self.partial_latest()
        result = self.run_cli('--all', '-v')
        self.assertOk(result)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[-2], '12  {0}/{0} processor*'.format(self.n), self.msg)
        self.assertEqual(lines[-1], '14  {}/{} processor*  [MISSING: {}...]'.format(
            self.n - len(lacking), self.n,
            ', '.join('processor{}'.format(i) for i in lacking[:5])), self.msg)

    def test_crashed_run_keep_last_skips_partial_time(self):
        lacking, procs = self.partial_latest()
        result = self.run_cli('--keep-last', '2')
        self.assertOk(result)
        self.assertEqual([line.split() for line in result.stdout.splitlines()],
                         [[t, 'delete'] for t in WRITE_TIMES[:4]]
                         + [['10', 'keep'], ['12', 'keep'], ['14', 'keep', '(partial)']], self.msg)

        self.assertOk(self.run_cli('--keep-last', '2', '--rm'))
        for i, proc in enumerate(procs):
            self.assertTimes(proc, ['0', '10', '12'] + ([] if i in lacking else ['14']))

    def test_crashed_run_rm_skips_processors_without_time(self):
        lacking, procs = self.partial_latest()
        before = snapshot(self.root)
        result = self.run_cli('--rm')
        self.assertOk(result)
        self.assertEqual(result.stdout, 'Deleted time(s): 14\n')
        self.assertRemovedOnly(before, ['processor{}/14'.format(i)
                                        for i in range(self.n) if i not in lacking])

    def test_dry_run_prints_each_existing_dir_in_processor_order(self):
        lacking, _ = self.partial_latest()
        before = snapshot(self.root)
        result = self.run_cli('--dry-run')
        self.assertOk(result)
        self.assertEqual(result.stdout.splitlines(),
                         ['[dry-run] Would delete: {}'.format(self.root / 'processor{}'.format(i) / '14')
                          for i in range(self.n) if i not in lacking], self.msg)
        self.assertEqual(snapshot(self.root), before, self.msg)

    def test_time_on_some_processors_deleted_where_present(self):
        lacking = set(self.rng.sample(range(self.n), self.rng.randint(1, self.n - 1)))
        procs = self.build(missing={'8': lacking})
        self.assertOk(self.run_cli('--time', '8.0', '--rm'))
        for proc in procs:
            self.assertTimes(proc, ['0'] + [t for t in WRITE_TIMES if t != '8'])


class CollatedTest(SyntheticCaseTest):

    def build(self, **kwargs):
        return ofcase.collated_case(self.root, self.n, **kwargs).procs

    def test_lists_both_sides_in_two_columns(self):
        self.build()
        result = self.run_cli('--all', '-v')
        self.assertOk(result)
        self.assertEqual(result.stdout.splitlines(),
                         ['{:<2}  root  processors{}'.format(t, self.n) for t in WRITE_TIMES])
        self.assertIn('root, processors{}'.format(self.n), result.stderr)

    def test_rm_without_side_touches_nothing(self):
        self.build()
        before = snapshot(self.root)
        self.assertFails(self.run_cli('--keep-last', '1', '--rm'), '--root, --decomposed, or both')
        self.assertEqual(snapshot(self.root), before, self.msg)

    def test_decomposed_prune_after_reconstruct_keeps_root(self):
        coll, = self.build()
        before = snapshot(self.root)
        self.assertOk(self.run_cli('--decomposed', '--keep-last', '2', '--rm'))
        self.assertTimes(coll, ['0', '12', '14'])
        self.assertRemovedOnly(before, ['processors{}/{}'.format(self.n, t) for t in WRITE_TIMES[:5]])
        self.assertTrue((self.root / 'processor0').is_dir())

    def test_both_sides_time_rm(self):
        self.build()
        before = snapshot(self.root)
        self.assertOk(self.run_cli('--root', '--decomposed', '--time', '8', '--rm'))
        self.assertRemovedOnly(before, ['8', 'processors{}/8'.format(self.n)])

    def test_partial_reconstruction_listing(self):
        done = WRITE_TIMES[:self.rng.randint(1, len(WRITE_TIMES) - 1)]
        self.build(reconstructed=done)
        result = self.run_cli('--all', '-v')
        self.assertOk(result)
        self.assertEqual(result.stdout.splitlines(),
                         ['{:<2}  {:<4}  processors{}'.format(t, 'root' if t in done else '', self.n)
                          for t in WRITE_TIMES], self.msg)
        result = self.run_cli('--root', '--all')
        self.assertEqual(result.stdout.split(), done, self.msg)

    def test_io_rank_dirs_form_one_side(self):
        k = self.rng.randint(2, 8)
        lacking = sorted(self.rng.sample(range(k), 2))
        dirs = self.build(io_ranks=k, reconstructed=(), missing={'14': lacking})
        ranges = ofcase.rank_ranges(self.n, k)
        self.assertEqual(sum(b - a + 1 for a, b in ranges), self.n)

        result = self.run_cli('--all', '-v')
        self.assertOk(result)
        self.assertEqual(result.stdout.splitlines()[-1],
                         '14  {}/{} processors{}_*  [MISSING: {}]'.format(
                             k - 2, k, self.n,
                             ', '.join('processors{}_{}-{}'.format(self.n, *ranges[i]) for i in lacking)),
                         self.msg)

        self.assertOk(self.run_cli('--keep-last', '1', '--rm'))
        for i, loc in enumerate(dirs):
            self.assertTimes(loc, ['0', '12'] + ([] if i in lacking else ['14']))


class GeneratorTest(TempDirTestCase):
    """Guards the fixtures against drifting from the real OpenFOAM layouts."""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'case'

    def entries(self, path):
        return sorted(p.name for p in path.iterdir())

    def test_time_names_match_openfoam(self):
        self.assertEqual(WRITE_TIMES, ['2', '4', '6', '8', '10', '12', '14'])
        self.assertEqual(ofcase.time_name(9.99999999999987566, 10), '10')
        self.assertEqual(ofcase.time_name(1e-05), '1e-05')
        names = ofcase.write_times(0.02, 0.02, 750, precision=10)
        self.assertEqual(names[:3] + names[-2:], ['0.02', '0.04', '0.06', '14.98', '15'])
        self.assertTrue(all(len(n.partition('.')[2]) <= 2 for n in names))

    def test_n_procs_is_reproducible_and_in_range(self):
        draws = [ofcase.n_procs(ofcase.case_rng('t{}'.format(i))) for i in range(50)]
        self.assertEqual(draws, [ofcase.n_procs(ofcase.case_rng('t{}'.format(i))) for i in range(50)])
        self.assertTrue(all(8 <= n <= 1024 for n in draws))
        self.assertGreater(len(set(draws)), 1)

    def test_undecomposed_layout(self):
        ofcase.undecomposed_case(self.root)
        self.assertEqual(self.entries(self.root), sorted(
            ['0', 'Allclean', 'Allrun', 'Cd.txt', 'constant', 'dynamicCode', 'log.blockMesh',
             'log.mirrorMesh', 'log.out', 'system', 'writeData'] + WRITE_TIMES))
        self.assertEqual(self.entries(self.root / '0'), ['U', 'p', 'tau', 'theta'])
        self.assertEqual(self.entries(self.root / '10'), [
            'U.gz', 'eigVals.gz', 'eigVecs.gz', 'p.gz', 'phi.gz', 'tau.gz', 'theta.gz', 'uniform'])
        self.assertEqual(self.entries(self.root / '10' / 'uniform'), ['time'])

    def test_decomposed_layout(self):
        ofcase.decomposed_case(self.root, 4)
        self.assertEqual(self.entries(self.root), sorted(
            ['0', 'Allclean', 'Allrun', 'Cd.txt', 'constant', 'dynamicCode', 'log.blockMesh',
             'log.decomposePar', 'log.mirrorMesh', 'log.out', 'processor0', 'processor1',
             'processor2', 'processor3', 'pview.foam', 'system', 'writeData']))
        proc = self.root / 'processor3'
        self.assertEqual(self.entries(proc), sorted(['0', 'constant'] + WRITE_TIMES))
        self.assertEqual(self.entries(proc / '0'), ['U.gz', 'p.gz', 'tau.gz', 'theta.gz'])
        self.assertIn('cellProcAddressing.gz', self.entries(proc / 'constant' / 'polyMesh'))

    def test_collated_layout(self):
        ofcase.collated_case(self.root, 4)
        self.assertEqual(self.entries(self.root), sorted(
            ['0', 'Allclean', 'Allrun', 'Cd.txt', 'case.foam', 'constant', 'dynamicCode',
             'log.blockMesh', 'log.decomposePar', 'log.mirrorMesh', 'log.out', 'processor0',
             'processors4', 'reconstructPar.log', 'system', 'writeData'] + WRITE_TIMES))
        self.assertEqual(self.entries(self.root / 'processor0'), [])
        coll = self.root / 'processors4'
        self.assertEqual(self.entries(coll), sorted(['0', 'constant'] + WRITE_TIMES))
        self.assertEqual(self.entries(coll / '0'), ['U', 'p', 'tau', 'theta'])
        self.assertEqual(self.entries(coll / '10'), [
            'U.gz', 'eigVals.gz', 'eigVecs.gz', 'p.gz', 'phi.gz', 'tau.gz', 'theta.gz', 'uniform'])
        self.assertIn('cellProcAddressing', self.entries(coll / 'constant' / 'polyMesh'))

    def test_rank_ranges_partition_processors(self):
        self.assertEqual(ofcase.rank_ranges(4, 2), [(0, 1), (2, 3)])
        self.assertEqual(ofcase.rank_ranges(10, 3), [(0, 2), (3, 5), (6, 9)])


if __name__ == '__main__':
    unittest.main()
