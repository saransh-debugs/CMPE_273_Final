"""Smoke tests for offline script paths (--mock)."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def _run_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    script = ROOT / "scripts" / name
    return subprocess.run(
        [PY, str(script), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestCoverageGateMock(unittest.TestCase):
    def test_mock_passes(self) -> None:
        cp = _run_script("coverage_gate.py", "--mock")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("PASS", cp.stdout)


class TestTokenAuditMock(unittest.TestCase):
    def test_mock_flags_by_default(self) -> None:
        cp = _run_script("token_audit.py", "--mock")
        self.assertEqual(cp.returncode, 1)

    def test_mock_passes_with_loose_threshold(self) -> None:
        cp = _run_script("token_audit.py", "--mock", "--threshold", "100")
        self.assertEqual(cp.returncode, 0, cp.stderr)


class TestCalibrationReportMock(unittest.TestCase):
    def test_mock_exit_zero(self) -> None:
        cp = _run_script("calibration_report.py", "--mock")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("well_calibrated", cp.stdout.lower())

    def test_mock_json(self) -> None:
        cp = _run_script("calibration_report.py", "--mock", "--json")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("verdict", cp.stdout)
