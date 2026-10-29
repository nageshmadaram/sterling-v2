"""Immutable non-operational governance package checks; no external services."""
import copy
import unittest
from scripts.verify_increment6_closeout import load_package, verify

class FrozenCloseoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.good=load_package()

    def deny(self, fn, *, document=None):
        x=copy.deepcopy(self.good)
        fn(x)
        with self.assertRaises((ValueError, KeyError, TypeError)):
            verify(x,document=document)

    def test_source_review_remains_no_go(self):
        r=verify(copy.deepcopy(self.good))
        self.assertEqual((r["controls_accepted"],r["controls_blocked"]),(0,12))
        self.assertEqual(r["production_deployment"],"NO-GO")
        self.assertEqual(r["human_owners_confirmed"],0)
        self.assertEqual(r["source_files_verified"],22)
        self.assertTrue(all(len(digest)==64 for digest in r["source_sha256"].values()))

    def test_control_accepted_refused(self):
        self.deny(lambda x:x["manifest"]["per_control"][0].update(status="ACCEPTED"))

    def test_control_dropped_refused(self):
        self.deny(lambda x:x["manifest"]["per_control"].pop())

    def test_control_evidence_dropped_refused(self):
        self.deny(lambda x:x["manifest"]["per_control"][1]["required_evidence"].pop())

    def test_missing_conditions_removed_refused(self):
        self.deny(lambda x:x["manifest"]["per_control"][2]["missing_conditions"].pop())

    def test_exception_reference_dropped_refused(self):
        self.deny(lambda x:x["manifest"]["per_control"][3].update(exception_refs=[]))

    def test_invented_team_confirmation_refused(self):
        self.deny(lambda x:x["manifest"]["per_control"][4].update(team_confirmed=True))

    def test_invented_human_owner_refused(self):
        self.deny(lambda x:x["manifest"]["per_control"][5].update(named_owner_confirmed=True))

    def test_invented_collector_refused(self):
        self.deny(lambda x:x["manifest"]["per_control"][6].update(collector_confirmed=True))

    def test_invented_reviewer_refused(self):
        self.deny(lambda x:x["manifest"]["per_control"][7].update(reviewer_confirmed=True))

    def test_fake_receipt_refused(self):
        self.deny(lambda x:x["manifest"]["per_control"][8].update(independent_receipt_count=1))

    def test_fake_signoff_refused(self):
        self.deny(lambda x:x["manifest"]["signoff_stages"][0].update(status="PROVIDED"))

    def test_missing_signoff_stage_refused(self):
        self.deny(lambda x:x["manifest"]["signoff_stages"].pop())

    def test_production_go_refused(self):
        self.deny(lambda x:x["manifest"]["decisions"].update({"PRODUCTION DEPLOYMENT":"GO"}))

    def test_merge_unfreeze_refused(self):
        self.deny(lambda x:x["manifest"]["decisions"].update(merge_to_main="AUTHORIZED"))

    def test_artifact_misclassified_as_independent_receipt_refused(self):
        self.deny(lambda x:x["manifest"]["sources"][0].update(independent_production_receipt=True))

    def test_duplicate_source_refused(self):
        self.deny(lambda x:x["manifest"]["sources"].append(copy.deepcopy(x["manifest"]["sources"][0])))

    def test_path_escape_refused(self):
        self.deny(lambda x:x["manifest"]["sources"][0].update(path="../../operator-secrets"))

    def test_fake_physical_device_refused(self):
        self.deny(lambda x:x["manifest"]["provenance"].update(physical_device_sessions=1))

    def test_changing_schema_refused(self):
        self.deny(lambda x:x["manifest"].update(schema="production-approved"))

    def test_spoofed_release_claim_in_document_refused(self):
        from scripts.verify_increment6_closeout import REPORT
        s=REPORT.read_text(encoding="utf-8")
        self.deny(lambda x:None,document=s+"\nPRODUCTION DEPLOYMENT: GO\n")

    def test_missing_freeze_statement_in_document_refused(self):
        self.deny(lambda x:None,document="OFFLINE VALIDATION: PASS")


if __name__ == "__main__":
    unittest.main()
