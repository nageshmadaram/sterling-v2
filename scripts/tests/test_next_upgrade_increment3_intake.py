"""Fail-closed governance intake structural checks. No real systems or approvals."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/evidence/2026-10-29-next-upgrade"
EXPECTED = ("H01","T02","S03","I04","D05","R06","B07","M08","A09","L10","F11","G12")


class IncrementThreeGovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((EVIDENCE / "increment-3-governance-intake.json").read_text())
        cls.baseline = json.loads((EVIDENCE / "control-evidence-map.json").read_text())

    def test_all_controls_trace_without_claiming_acceptance(self):
        data = self.data
        self.assertEqual(data["record_kind"], "EVIDENCE_PREPARATION_NOT_PRODUCTION_ACCEPTANCE")
        self.assertEqual(tuple(c["control_id"] for c in data["controls"]), EXPECTED)
        self.assertEqual(tuple(c["id"] for c in self.baseline["controls"]), EXPECTED)
        for c, source in zip(data["controls"], self.baseline["controls"]):
            self.assertEqual(c["status"], "BLOCKED")
            self.assertEqual(c["accountable_role_from_baseline"], source["accountable_role"])
            self.assertEqual(c["expected_independent_evidence"], source["evidence_required"])
            self.assertEqual(c["scope"], "READ_ONLY_PROCEDURE_DRAFT_NOT_EXECUTED")
            self.assertEqual(c["team_assignment"], "UNCONFIRMED")
            self.assertTrue(c["proposed_team"])
            self.assertTrue(c["independent_review_role"])
            self.assertTrue(c["read_only_procedure"])
            self.assertIsNone(c["named_human_owner"])
            self.assertIsNone(c["named_evidence_collector"])
            self.assertIsNone(c["named_independent_reviewer"])
            self.assertIsNone(c["observation_environment"])
            self.assertIsNone(c["observed_at_utc"])
            self.assertEqual(c["signoff"], "NOT_PROVIDED")
            self.assertEqual(c["received_independent_receipts"], [])

    def test_assigned_teams_and_human_signatures_cannot_be_inferred(self):
        data = self.data
        self.assertFalse(data["team_assignments_are_verified"])
        self.assertEqual(data["named_owners_assigned"], 0)
        self.assertEqual(data["independent_human_signatures"], 0)
        self.assertEqual(data["actual_production_receipts"], 0)
        self.assertEqual(data["receipt_contract"]["current_receipt_count"], 0)
        self.assertTrue(data["receipt_contract"]["receipt_approval_out_of_scope"])

    def test_release_freeze_and_blocked_governance_gates(self):
        d = self.data
        s = d["safety"]
        self.assertTrue(s["freeze_active"])
        self.assertEqual(s["offline_validation"], "PASS")
        self.assertEqual(s["production_certification"], "NOT_ACHIEVED")
        self.assertEqual(s["deployment"], "NO_GO")
        self.assertEqual(s["live_trading"], "NOT_AUTHORIZED")
        self.assertEqual(s["broker_order_routing"], "NOT_AUTHORIZED")
        self.assertEqual(s["merge_to_main"], "NOT_AUTHORIZED")
        self.assertFalse(s["real_host_access_performed"])
        self.assertFalse(s["broker_connection_performed"])
        self.assertFalse(s["production_operations_performed"])
        self.assertTrue(all(row["state"] != "APPROVED" for row in d["governance_review_checklist"]))

    def test_increment3_validation_receipt_is_source_only_and_blocked(self):
        receipt = json.loads((EVIDENCE / "increment-3-validation-result.json").read_text())
        self.assertEqual(receipt["record_kind"], "RUN_ATTESTATION_SOURCE_AND_SIMULATED_ONLY")
        self.assertEqual(receipt["passing_run"]["conclusion"], "success")
        self.assertEqual(receipt["passing_run"]["chromium"]["passed"], 6)
        self.assertTrue(receipt["passing_run"]["chromium"]["api_mocked"])
        self.assertFalse(receipt["passing_run"]["chromium"]["physical_device"])
        self.assertEqual(receipt["passing_run"]["backend"]["pytest_passed"], 64)
        self.assertFalse(receipt["passing_run"]["frontend"]["published"])
        self.assertEqual(receipt["product_runtime_files_edited_in_increment3"], [])
        self.assertEqual(receipt["controls"], {"accepted": 0, "blocked": 12, "h01_production": "BLOCKED"})
        self.assertEqual(receipt["human_named_control_owners"], 0)
        self.assertEqual(receipt["independent_real_environment_receipts"], 0)
        self.assertEqual(receipt["independent_reviewer_signatures"], 0)
        d = receipt["decisions"]
        self.assertEqual((d["offline_validation"],d["production_deployment"],d["live_trading"],d["broker_order_routing"]),
            ("PASS","NO_GO","NOT_AUTHORIZED","NOT_AUTHORIZED"))
        self.assertEqual((d["production_certification"],d["release_freeze"],d["main_merge"]),
            ("NOT_ACHIEVED","ACTIVE","NOT_AUTHORIZED"))


if __name__ == "__main__":
    unittest.main()
