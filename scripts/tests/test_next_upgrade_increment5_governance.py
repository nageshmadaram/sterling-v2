"""Final nonoperational packet negative tests; do not observe or act on real systems."""
import copy
import unittest

from scripts.verify_increment5_governance import load_package, verify


class FinalGovernancePacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = load_package()

    def deny(self, mutation):
        candidate = copy.deepcopy(self.snapshot)
        mutation(candidate)
        with self.assertRaises((ValueError, KeyError, TypeError)):
            verify(candidate)

    def test_valid_packet_remains_no_go(self):
        result = verify(copy.deepcopy(self.snapshot))
        self.assertEqual((result["controls_accepted"],result["controls_blocked"]),(0,12))
        self.assertEqual(result["production_deployment"],"NO-GO")
        self.assertFalse(result["external_host_or_provider_access_performed"])

    def test_fabricated_owner_denied(self):
        self.deny(lambda x:x["roles"]["controls"][0].update(owner_person="Fake person"))

    def test_team_claimed_accepted_denied(self):
        self.deny(lambda x:x["roles"]["controls"][1].update(team_appointment_accepted=True))

    def test_fabricated_independent_reviewer_denied(self):
        self.deny(lambda x:x["packet"]["controls"][2]["responsibility"].update(independent_reviewer_person="Fake reviewer"))

    def test_collector_identity_injected_denied(self):
        self.deny(lambda x:x["roles"]["controls"][3].update(collector_person="Fake collector"))

    def test_reviewer_separation_weakened_denied(self):
        self.deny(lambda x:x["roles"]["controls"][4]["separation_policy"].update(independent_reviewer_must_differ_from_owner_and_collector=False))

    def test_control_accepted_denied(self):
        self.deny(lambda x:x["packet"]["controls"][5].update(status="ACCEPTED"))

    def test_control_removed_denied(self):
        self.deny(lambda x:x["packet"]["controls"].pop())

    def test_independent_requirement_deleted_denied(self):
        self.deny(lambda x:x["packet"]["controls"][6]["missing_conditions"].pop(2))

    def test_receipt_fabricated_denied(self):
        self.deny(lambda x:x["packet"]["controls"][7]["currently_received_real_receipts"].append("fake"))

    def test_exception_disappears_denied(self):
        self.deny(lambda x:x["packet"]["exceptions"].pop())

    def test_historical_exception_confused_with_current_denied(self):
        self.deny(lambda x:x["packet"]["exceptions"][4].update(current_interpretation="All accepted"))

    def test_mfa_claim_denied(self):
        self.deny(lambda x:x["packet"]["additional_unmet_gates"]["mfa_bound_stepup"].update(status="PASS"))

    def test_physical_device_claim_denied(self):
        self.deny(lambda x:x["packet"]["additional_unmet_gates"]["physical_mobile"].update(physical_sessions=1))

    def test_external_receipt_form_pretends_signed_denied(self):
        self.deny(lambda x:x["packet"]["external_receipt_template"].update(independent_review_signature="fake-signature"))

    def test_signoff_injected_denied(self):
        self.deny(lambda x:x["packet"]["signoff_checklist"][2].update(named_approver="fake"))

    def test_main_merge_enabled_denied(self):
        self.deny(lambda x:x["packet"]["prohibited_actions"].update(merge_main=True))

    def test_deployment_go_denied(self):
        self.deny(lambda x:x["packet"]["decision"].update({"PRODUCTION DEPLOYMENT":"GO"}))

    def test_live_trading_unlocked_denied(self):
        self.deny(lambda x:x["packet"]["decision"].update({"LIVE TRADING":"AUTHORIZED"}))

    def test_governance_signoff_claim_denied(self):
        self.deny(lambda x:x["packet"]["decision"].update(independent_release_signoff="PROVIDED"))

    def test_traceable_source_required(self):
        self.deny(lambda x:x["packet"]["source_documents"].__setitem__(0,"docs/does-not-exist.md"))


if __name__ == "__main__":
    unittest.main()
