import re
import unittest
from pathlib import Path

import portbridge

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


class TestVersion(unittest.TestCase):
    def test_package_version_matches_pyproject(self):
        match = re.search(r'^version\s*=\s*"([^"]+)"', _PYPROJECT.read_text(), re.MULTILINE)

        self.assertIsNotNone(match)
        self.assertEqual(portbridge.__version__, match.group(1))


if __name__ == "__main__":
    unittest.main()
