"""Direct CLI invocation works with no package-install or PYTHONPATH tricks."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "certification" / "verify_h01_observation.py"


class H01StandaloneCommandTests(unittest.TestCase):
    def test_cli_help_starts_outside_repository_without_pythonpath(self):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            cwd="/",
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("--environment-id", completed.stdout)
        self.assertIn("--authorization-ref", completed.stdout)

    def test_cli_missing_evidence_fails_closed(self):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [
                sys.executable, str(SCRIPT), "--observation",
                "/nonexistent/sterling-h01-synthetic-report.json",
                "--environment-id", "env-001",
                "--authorization-ref", "approval-001",
            ],
            cwd="/",
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(2, completed.returncode)
        self.assertIn('"control_acceptance": "BLOCKED"', completed.stdout)
        self.assertIn('"production_deployment": "NO_GO"', completed.stdout)


if __name__ == "__main__":
    unittest.main()
