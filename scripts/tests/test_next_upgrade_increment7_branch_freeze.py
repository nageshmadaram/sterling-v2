"""Frozen governance source lock and negative-path tests; never connect to providers."""
import copy
import unittest
from pathlib import Path
from scripts.verify_increment7_branch_freeze import (
    ALLOWED, MAIN, ROOT, load_package, verify,
)

class FinalBranchFreezeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_package()
        cls.doc = (ROOT/"docs/implementation/2026-10-29-final-frozen-branch-review-increment-7.md").read_text()

    def deny(self, mutate, **kwargs):
        candidate = copy.deepcopy(self.data)
        mutate(candidate)
        options = {"changed_paths":list(ALLOWED), "observed_main":MAIN, "doc":self.doc}
        options.update(kwargs)
        with self.assertRaises((ValueError, KeyError, TypeError)):
            verify(candidate, **options)

    def test_valid_source_lock_and_hygiene_are_nonoperational(self):
        result = verify(copy.deepcopy(self.data),changed_paths=list(ALLOWED),observed_main=MAIN,doc=self.doc)
        self.assertEqual(result["locked_sources"],26)
        self.assertEqual((result["accepted_controls"],result["blocked_controls"]),(0,12))
        self.assertEqual(result["branch_hygiene"],"PASS")
        self.assertEqual(result["main_unchanged"],"PASS")
        self.assertTrue(all(len(v)==64 for v in result["source_sha256"].values()))

    def test_reject_modified_pinned_blob_identity(self):
        self.deny(lambda d:d["lock"]["source_entries"][0].update(git_blob_sha1="0"*40))

    def test_reject_real_source_bytes_mismatch(self):
        from scripts.verify_increment7_branch_freeze import safe_source
        def read(rel):
            raw = safe_source(rel).read_bytes()
            return raw+b"tamper" if rel.endswith("next-upgrade-spec.md") else raw
        self.deny(lambda d:None,source_reader=read)

    def test_reject_removed_historical_source(self):
        self.deny(lambda d:d["lock"]["source_entries"].pop())

    def test_reject_duplicate_source(self):
        self.deny(lambda d:d["lock"]["source_entries"].append(d["lock"]["source_entries"][0]))

    def test_reject_path_traversal(self):
        self.deny(lambda d:d["lock"]["source_entries"][0].update(path="../secret"))

    def test_reject_source_renamed_or_reclassified(self):
        self.deny(lambda d:d["lock"]["source_entries"][1].update(classification="PRODUCTION_RECEIPT"))

    def test_reject_changed_release_decision(self):
        self.deny(lambda d:d["lock"]["decisions"].update({"PRODUCTION DEPLOYMENT":"GO"}))

    def test_reject_enabled_broker_order_routing(self):
        self.deny(lambda d:d["lock"]["decisions"].update({"BROKER ORDER ROUTING":"AUTHORIZED"}))

    def test_reject_invented_human_owner(self):
        self.deny(lambda d:d["lock"]["governance"].update(confirmed_human_owners=1))

    def test_reject_invented_independent_receipt(self):
        self.deny(lambda d:d["lock"]["governance"].update(real_environment_receipts=1))

    def test_reject_invented_signoff(self):
        self.deny(lambda d:d["lock"]["governance"].update(independent_signoffs=1))

    def test_reject_incomplete_control_ids(self):
        self.deny(lambda d:d["previous"]["manifest"]["per_control"].pop())

    def test_reject_unexpected_runtime_diff(self):
        self.deny(lambda d:None,changed_paths=list(ALLOWED)+["backend/app/main.py"])

    def test_reject_missing_allowlisted_diff_file(self):
        self.deny(lambda d:None,changed_paths=list(ALLOWED)[:-1])

    def test_reject_unexpected_main_commit(self):
        self.deny(lambda d:None,observed_main="1"*40)

    def test_reject_broadened_file_allowlist(self):
        self.deny(lambda d:d["lock"]["allowlisted_changes_since_baseline"].append("backend/app/main.py"))

    def test_reject_duplicate_change_path(self):
        self.deny(lambda d:None,changed_paths=list(ALLOWED)+[ALLOWED[0]])

    def test_reject_wrong_baseline(self):
        self.deny(lambda d:d["lock"].update(baseline_commit="2"*40))

    def test_reject_misleading_final_doc_claim(self):
        self.deny(lambda d:None,doc=self.doc+"\nPRODUCTION DEPLOYMENT: GO\n")

    def test_reject_missing_no_go_doc(self):
        self.deny(lambda d:None,doc="OFFLINE VALIDATION: PASS")

    def test_reject_source_identity_claimed_as_production_proof(self):
        self.deny(lambda d:d["lock"].update(review_scope="PRODUCTION_APPROVED"))

    def test_reject_missing_final_freeze_text(self):
        self.deny(lambda d:d["lock"].update(final_sentence="Release approved."))

if __name__ == "__main__":
    unittest.main()
