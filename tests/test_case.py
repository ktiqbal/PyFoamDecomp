import unittest

from ofcase import TempDirTestCase
from pyfoamdecomp import case


class IsTimeDirTest(unittest.TestCase):

    def test_accepts_openfoam_time_names(self):
        for name in ('0', '10', '0.5', '1e-3', '1.5e+2', '2E-05'):
            self.assertTrue(case.is_time_dir(name), name)

    def test_rejects_other_case_entries(self):
        for name in ('constant', 'system', 'processor0', '-1', '1.', '.5', '1e', '0_old', ''):
            self.assertFalse(case.is_time_dir(name), name)


class ProcessorFamiliesTest(TempDirTestCase):

    def make(self, *names):
        for name in names:
            (self.tmp / name).mkdir()

    def families(self):
        return {k: [p.name for p in v] for k, v in case.find_processor_families(self.tmp).items()}

    def test_classic_and_collated_families(self):
        self.make('processor10', 'processor2', 'processor0', 'processors4', 'processors8_0-3',
                  'processors8_4-7', 'processors', 'constant')
        (self.tmp / 'processor5').touch()
        self.assertEqual(self.families(), {
            'processor': ['processor0', 'processor2', 'processor10'],
            'processors4': ['processors4'],
            'processors8': ['processors8_0-3', 'processors8_4-7'],
        })

    def test_io_rank_dirs_sorted_by_first_rank(self):
        self.make('processors12_10-11', 'processors12_0-1', 'processors12_2-9')
        self.assertEqual(self.families()['processors12'],
                         ['processors12_0-1', 'processors12_2-9', 'processors12_10-11'])

    def test_no_processor_dirs(self):
        self.make('0', 'constant', 'system')
        self.assertEqual(self.families(), {})


class TimesTest(TempDirTestCase):

    def setUp(self):
        super().setUp()
        self.procs = [self.tmp / 'processor0', self.tmp / 'processor1']
        for proc, names in zip(self.procs, (['0', '1e-3', '8', '10'], ['0', '0.5', '8'])):
            for name in names + ['constant']:
                (proc / name).mkdir(parents=True)
        (self.procs[0] / '20').touch()

    def test_union_is_numerically_sorted_dirs_only(self):
        self.assertEqual(case.get_times_union([self.procs]), ['1e-3', '0.5', '8', '10'])
        self.assertEqual(case.get_times_union([self.procs], include_zero=True)[0], '0')

    def test_find_time_by_value_returns_dir_name(self):
        self.assertEqual(case.find_time_by_value('8.0', [self.procs]), ['8'])
        self.assertEqual(case.find_time_by_value('0.001', [self.procs]), ['1e-3'])
        self.assertEqual(case.find_time_by_value('9', [self.procs]), [])

    def test_is_complete_needs_every_location_of_a_side(self):
        self.assertTrue(case.is_complete('8', [self.procs]))
        self.assertFalse(case.is_complete('10', [self.procs]))
        self.assertTrue(case.is_complete('10', [self.procs, [self.procs[0]]]))


if __name__ == '__main__':
    unittest.main()
