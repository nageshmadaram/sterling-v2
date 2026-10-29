"""Negative mutation checks for Increment 4 blocked governance state (stdlib only)."""
import copy
import unittest
from scripts.verify_increment4_governance import load_package, verify


class IncrementFourEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.good = load_package()

    def test_valid_structural_package_still_denies_operational_authority(self):
        r = verify(copy.deepcopy(self.good))
        self.assertEqual(r["validation"], "PASS")
        self.assertEqual((r["controls_accepted"], r["controls_blocked"]), (0, 12))
        self.assertEqual(r["production_deployment"], "NO_GO")

    def reject(self, mutate):
        data = copy.deepcopy(self.good)
        mutate(data)
        with self.assertRaises((ValueError, KeyError, TypeError)):
            verify(data)

    def test_reject_fake_named_human_owner(self):
        self.reject(lambda x: x["owner"]["controls"][0].update(named_accountable_owner="Invented Reviewer"))

    def test_reject_confirmed_team_without_acknowledgment(self):
        self.reject(lambda x: x["owner"]["controls"][1].update(team_confirmed=True))

    def test_reject_forged_control_acceptance(self):
        self.reject(lambda x: x["governance"]["control_register"][2].update(status="ACCEPTED"))

    def test_reject_fake_independent_receipt(self):
        self.reject(lambda x: x["owner"]["controls"][3]["received_receipts"].append("fake-proof"))

    def test_reject_forged_independent_reviewer(self):
        self.reject(lambda x: x["owner"]["controls"][4].update(named_independent_reviewer="Invented"))

    def test_reject_wrong_control_set(self):
        self.reject(lambda x: x["owner"]["controls"].pop())

    def test_reject_false_mfa_acceptance(self):
        self.reject(lambda x: x["session"].update(mfa_implemented=True))

    def test_reject_fabricated_mfa_stepup(self):
        self.reject(lambda x: x["session"].update(step_up_implemented=True))

    def test_reject_physical_device_acceptance_without_observation(self):
        self.reject(lambda x: x["device"].update(physical_acceptance="ACHIEVED"))

    def test_reject_fake_device_signoff(self):
        self.reject(lambda x: x["device"]["cases"][0].update(signed_test_result="fake"))

    def test_reject_merge_authority(self):
        self.reject(lambda x: x["governance"]["strict_decision"].update(merge_to_main="AUTHORIZED"))

    def test_reject_deployment_authority(self):
        self.reject(lambda x: x["governance"]["actions"].update(deployment=True))

    def test_reject_independent_signoff_fiction(self):
        self.reject(lambda x: x["governance"]["strict_decision"].update(independent_signoff="PROVIDED"))

    def test_increment4_closeout_records_only_verified_run_and_no_go(self):
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parents[2] / "docs/evidence/2026-10-29-next-upgrade/increment-4-closeout.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["record_kind"], "REVIEW_READY_NO_GO_NOT_ACCEPTED")
        self.assertEqual(record["validation"]["conclusion"], "success")
        self.assertEqual(record["validation"]["backend_disposable_tests"]["passed"], 66)
        self.assertEqual(record["validation"]["chromium_isolated_tests"]["passed"], 6)
        self.assertFalse(record["validation"]["chromium_isolated_tests"]["physical_device"])
        self.assertEqual(record["validation"]["source_negative_and_structural_tests"], 33)
        self.assertEqual(record["control_state"], {"accepted":0,"blocked":12,
            "confirmed_named_owners":0,"confirmed_teams":0,"independent_signoffs":0,"real_operational_receipts":0})
        self.assertEqual(record["decision"], {"offline_validation":"PASS","production_deployment":"NO-GO",
            "live_trading":"NOT AUTHORIZED","broker_order_routing":"NOT AUTHORIZED",
            "production_certification":"NOT ACHIEVED","release_freeze":"ACTIVE"})
        self.assertTrue(all(count == 0 for count in record["operation_occurrences"].values()))


if __name__ == "__main__":
    unittest.main()
