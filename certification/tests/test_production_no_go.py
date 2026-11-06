"""Negative tests for the source-only NO-GO governance intake, no external access."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from certification.verify_production_no_go import evaluate_manifest

REGISTER = (
    Path(__file__).resolve().parents[1] / "production_evidence_register.json"
)


class ReviewNoGoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = json.loads(REGISTER.read_text(encoding="utf-8"))

    def changed(self):
        return copy.deepcopy(self.baseline)

    def test_original_blocked_register_is_consistent(self):
        self.assertEqual([], evaluate_manifest(self.baseline))
        self.assertEqual(12, len(self.baseline["controls"]))

    def test_cannot_promote_production_or_broker_authority(self):
        for key in (
            "real_host_verified", "production_certs_verified",
            "live_trading_authorized", "broker_order_routing_authorized",
            "production_deployment_authorized", "main_merge_authorized",
        ):
            with self.subTest(field=key):
                m = self.changed()
                m[key] = True
                self.assertTrue(evaluate_manifest(m))

    def test_change_from_no_go_is_rejected(self):
        for field, value in (
            ("production_decision", "GO"),
            ("live_decision", "AUTHORIZED"),
            ("reviewer_signoff", "APPROVED"),
            ("offline_evidence_is_production_evidence", True),
        ):
            with self.subTest(field=field):
                m = self.changed()
                m[field] = value
                self.assertTrue(evaluate_manifest(m))

    def test_missing_or_duplicated_controls_fail(self):
        for mutate in (
            lambda m: m["controls"].pop(),
            lambda m: m["controls"][1].update(
                id=m["controls"][0]["id"]
            ),
        ):
            m = self.changed()
            mutate(m)
            self.assertTrue(evaluate_manifest(m))

    def test_unreviewed_evidence_cannot_implicitly_close_gate(self):
        m = self.changed()
        m["controls"][0]["status"] = "PASS"
        m["controls"][0]["independent_evidence"] = [
            {"url": "https://example.invalid/unsupported-proof"}
        ]
        self.assertTrue(evaluate_manifest(m))

    def test_unsigned_observer_and_signoff_are_blocked(self):
        for field, value in (
            ("observed_environment", "assumed production"),
            ("observed_at", "2026-10-29T00:00:00Z"),
            ("observer", "unverified"),
            ("reviewer", "unverified"),
        ):
            with self.subTest(field=field):
                m = self.changed()
                m["controls"][5][field] = value
                self.assertTrue(evaluate_manifest(m))

    def test_wrong_candidate_or_missing_acceptance_criteria_fail(self):
        m = self.changed()
        m["offline_candidate_sha"] = "unknown"
        self.assertTrue(evaluate_manifest(m))
        m = self.changed()
        m["controls"][3]["evidence_required"] = []
        self.assertTrue(evaluate_manifest(m))

    def test_arbitrary_data_not_a_valid_manifest(self):
        self.assertTrue(evaluate_manifest([]))
        self.assertTrue(evaluate_manifest("PASS"))
        self.assertTrue(evaluate_manifest({}))


if __name__ == "__main__":
    unittest.main()
