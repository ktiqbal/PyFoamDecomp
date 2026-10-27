import importlib
import sys
import unittest

from ofcase import REPO


@unittest.skipIf(sys.version_info < (3, 11), 'tomllib needs Python 3.11')
class EntryPointsTest(unittest.TestCase):

    def test_scripts_point_at_main_with_matching_prog(self):
        import tomllib
        scripts = tomllib.loads((REPO / 'pyproject.toml').read_text())['project']['scripts']
        self.assertEqual(set(scripts), {'foamTimes', 'foamLogParse'})
        for command, target in scripts.items():
            module_name, func = target.split(':')
            self.assertEqual(func, 'main', command)
            module = importlib.import_module(module_name)
            self.assertTrue(callable(module.main), command)
            self.assertEqual(module.build_parser().prog, command)


if __name__ == '__main__':
    unittest.main()
