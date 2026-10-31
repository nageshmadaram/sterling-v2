"""Governance schema negatives: validates structure, does not approve trading."""
import copy
import json
from pathlib import Path
import unittest

from scripts.validate_preproduction_controls import IDS, validate

ROOT = Path(__file__).resolve().parents[2] / "docs/ops"


class GovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controls = json.loads((ROOT / "PREPRODUCTION_CONTROLS.json").read_text())
        cls.evidence = json.loads((ROOT / "PREPRODUCTION_EVIDENCE_REGISTRY.json").read_text())

    def test_closed_structural_registry_is_still_no_go(self):
        self.assertEqual(validate(self.controls, self.evidence), [])
        self.assertEqual({r["id"] for r in self.controls["controls"]}, set(IDS))
        self.assertFalse(self.controls["live_trading_authorized"])

    def test_unauthorized_promotion_is_rejected(self):
        c = copy.deepcopy(self.controls)
        c["decision"] = "GO"
        c["controls"][0]["current_status"] = "ACCEPTED"
        self.assertTrue(validate(c, self.evidence))

    def test_missing_control_is_rejected(self):
        c = copy.deepcopy(self.controls)
        c["controls"].pop()
        self.assertTrue(validate(c, self.evidence))


if __name__ == "__main__":
    unittest.main()
