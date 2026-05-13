from __future__ import annotations

import sys
import unittest

from tests.test_api_main import TestTenantAuth
from tests.test_governance import TestGovernancePolicy


def build_suite() -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for test_case in [TestGovernancePolicy, TestTenantAuth]:
        suite.addTests(loader.loadTestsFromTestCase(test_case))
    return suite


def main() -> int:
    print("Running recent task tests:")
    result = unittest.TextTestRunner(verbosity=2).run(build_suite())
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())