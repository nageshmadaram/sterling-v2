"""Negative tests: an evidence-preparation PASS can never grant operational authority."""
import copy
import json
import unittest
from pathlib import Path
from scripts.verify_next_upgrade_evidence import PACKAGE, ROOT, validate_bundle


class EvidenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((PACKAGE / "release-package.json").read_text())
        cls.mapping = json.loads((PACKAGE / "control-evidence-map.json").read_text())
        cls.exceptions = json.loads((PACKAGE / "exceptions.json").read_text())

    def check(self, m=None, c=None, e=None):
        return validate_bundle(
            self.manifest if m is None else m,
            self.mapping if c is None else c,
            self.exceptions if e is None else e,
            ROOT,
        )

    def test_source_package_baseline_is_structurally_valid(self):
        self.assertEqual([], self.check())

    def test_reject_claimed_live_execution(self):
        m = copy.deepcopy(self.manifest)
        m["decisions"]["live_trading"] = "AUTHORIZED"
        self.assertTrue(self.check(m=m))

    def test_reject_release_freeze_bypass(self):
        m = copy.deepcopy(self.manifest)
        m["freeze_active"] = False
        self.assertTrue(self.check(m=m))

    def test_reject_promoted_offline_evidence(self):
        m = copy.deepcopy(self.manifest)
        m["offline_evidence_is_production_evidence"] = True
        self.assertTrue(self.check(m=m))

    def test_reject_accepted_control_without_receipt(self):
        c = copy.deepcopy(self.mapping)
        c["controls"][0]["status"] = "ACCEPTED"
        self.assertTrue(self.check(c=c))

    def test_reject_fabricated_owner_and_receipt(self):
        c = copy.deepcopy(self.mapping)
        c["controls"][3]["named_owner"] = "Assumed person"
        c["controls"][3]["received_independent_evidence"] = ["fabricated"]
        self.assertTrue(self.check(c=c))

    def test_reject_missing_control(self):
        c = copy.deepcopy(self.mapping)
        c["controls"].pop()
        self.assertTrue(self.check(c=c))

    def test_reject_duplicate_control(self):
        c = copy.deepcopy(self.mapping)
        c["controls"][1]["id"] = "H01"
        self.assertTrue(self.check(c=c))

    def test_reject_source_path_traversal(self):
        m = copy.deepcopy(self.manifest)
        m["features"][0]["source_files"] = ["../../credentials"]
        self.assertTrue(self.check(m=m))

    def test_reject_runtime_reverification_fiction(self):
        m = copy.deepcopy(self.manifest)
        m["features"][0]["runtime_reverified_this_slice"] = True
        self.assertTrue(self.check(m=m))

    def test_reject_closed_exception(self):
        e = copy.deepcopy(self.exceptions)
        e["exceptions"][0]["status"] = "CLOSED"
        self.assertTrue(self.check(e=e))

    def test_reject_mismatched_certification_snapshot(self):
        m = copy.deepcopy(self.manifest)
        m["certification_register_source"]["observed_commit"] = "missing"
        self.assertTrue(self.check(m=m))


if __name__ == "__main__":
    unittest.main()
