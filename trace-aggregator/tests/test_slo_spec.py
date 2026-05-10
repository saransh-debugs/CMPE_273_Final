"""Tests for slo.spec catalog."""
from __future__ import annotations

import unittest

from slo.spec import SLOS, SLOSpec, by_name


class TestSLOSpecPasses(unittest.TestCase):
    def test_leq(self) -> None:
        s = SLOSpec("a", "t", "", "x", 10.0, "<=", 5)
        self.assertTrue(s.passes(5.0))
        self.assertTrue(s.passes(10.0))
        self.assertFalse(s.passes(11.0))

    def test_geq(self) -> None:
        s = SLOSpec("a", "t", "", "x", 0.99, ">=", 5)
        self.assertTrue(s.passes(0.99))
        self.assertFalse(s.passes(0.5))

    def test_bad_comparison(self) -> None:
        s = SLOSpec("a", "t", "", "x", 1.0, "==", 5)
        with self.assertRaises(ValueError):
            s.passes(1.0)


class TestSLOSCatalog(unittest.TestCase):
    def test_unique_names(self) -> None:
        names = [s.name for s in SLOS]
        self.assertEqual(len(names), len(set(names)))

    def test_catalog_thresholds_sane(self) -> None:
        for s in SLOS:
            self.assertTrue(s.name)
            self.assertTrue(s.title)
            self.assertIn(s.comparison, ("<=", ">="))
            self.assertGreater(s.window_minutes, 0)

    def test_by_name_roundtrip(self) -> None:
        first = SLOS[0]
        self.assertEqual(by_name(first.name).name, first.name)
