"""Source-only checks for increment 2 evidence; no external operational I/O."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "docs/evidence/2026-10-29-next-upgrade/hardening-regression-evidence.json"
IDS = ("H01", "T02", "S03", "I04", "D05", "R06", "B07", "M08", "A09", "L10", "F11", "G12")

class HardeningEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = json.loads(FILE.read_text(encoding="utf-8"))

    def test_evidence_is_explicitly_source_and_synthetic_only(self):
        e = self.e
        self.assertEqual(e["scope"], "SOURCE_AND_SYNTHETIC_TESTS_ONLY")
        self.assertTrue(e["release_freeze_active"])
        self.assertFalse(e["final_validation"]["frontend"]["build_published"])
        self.assertFalse(e["final_validation"]["backend"]["live_execution_env"])
        self.assertTrue(e["final_validation"]["backend"]["disposable_database"])
        self.assertFalse(e["final_validation"]["backend"]["broker_network_requests"])

    def test_test_first_and_verified_ci_references(self):
        e = self.e
        for field in ("reproduction", "final_validation"):
            record = e[field]
            self.assertEqual(record["workflow_url"],
                "https://github.com/nageshmadaram/sterling-v2/actions/runs/" + str(record["workflow_run_id"]))
        self.assertEqual(e["reproduction"]["frontend_tests"], {"passed": 17, "failed": 4, "total": 21})
        self.assertEqual(e["final_validation"]["conclusion"], "success")
        self.assertEqual(e["final_validation"]["frontend"]["passed"], 21)
        self.assertEqual(e["final_validation"]["backend"]["passed"], 62)
        self.assertEqual(e["final_validation"]["source_evidence"]["passed"], 12)

    def test_every_control_remains_blocked(self):
        e = self.e
        self.assertEqual(tuple(e["controls"]), IDS)
        self.assertEqual(set(e["controls"].values()), {"BLOCKED"})
        self.assertEqual((e["accepted_controls"],e["blocked_controls"]), (0,12))
        self.assertEqual(e["independent_release_signoff"], "NOT_PROVIDED")
        self.assertEqual(e["production_certification"], "NOT_ACHIEVED")
        self.assertEqual(e["production_deployment"], "NO_GO")
        self.assertEqual(e["live_trading"], "NOT_AUTHORIZED")
        self.assertEqual(e["broker_order_routing"], "NOT_AUTHORIZED")
        self.assertEqual(e["main_merge"], "NOT_AUTHORIZED")
