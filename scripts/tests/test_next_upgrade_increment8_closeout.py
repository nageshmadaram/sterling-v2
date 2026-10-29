"""Mutation safety tests for final source-only review lock (no operational I/O)."""
from __future__ import annotations
import copy
import unittest
from scripts.verify_increment8_closeout import (
    BRANCH, BASE, MAIN, PATHS, HISTORICAL_LOCK_BLOB, load_package, verify,
)

def good_state():
    return {
        "branch":BRANCH,"head":BASE,"main":MAIN,
        "changed_paths":list(PATHS),"working_tree_clean":True,
        "historical_lock_blob":HISTORICAL_LOCK_BLOB,
        "file_modes":dict.fromkeys(PATHS,"100644"),
    }


class IncrementEightFinalReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.good = load_package()

    def reject(self, mutation, state=None, doc=None):
        data=copy.deepcopy(self.good)
        mutation(data)
        with self.assertRaises((ValueError,KeyError,TypeError)):
            verify(data,state=good_state() if state is None else state,doc=doc)

    def test_positive_structural_snapshot_is_no_go(self):
        r=verify(copy.deepcopy(self.good),state=good_state())
        self.assertEqual((r["accepted_controls"],r["blocked_controls"]),(0,12))
        self.assertEqual(r["locked_historical_sources"],26)
        self.assertEqual(r["branch_hygiene"],"PASS")
        self.assertEqual(r["production_deployment"],"NO-GO")

    def test_forged_release_decision_rejected(self):
        self.reject(lambda d:d["snapshot"]["decisions"].update({"PRODUCTION DEPLOYMENT":"GO"}))

    def test_live_execution_unlocked_rejected(self):
        self.reject(lambda d:d["snapshot"]["permissions"].update(live_trading=True))

    def test_fake_named_owner_rejected(self):
        self.reject(lambda d:d["snapshot"]["controls"][0].update(human_accountable_owner_confirmed=True))

    def test_fake_independent_reviewer_rejected(self):
        self.reject(lambda d:d["snapshot"]["controls"][1].update(independent_reviewer_confirmed=True))

    def test_fake_independent_receipt_rejected(self):
        self.reject(lambda d:d["snapshot"]["controls"][2].update(real_production_evidence_receipts=1))

    def test_forged_g12_signoff_rejected(self):
        self.reject(lambda d:d["snapshot"]["controls"][11].update(approval_signature="FAKE"))

    def test_control_removed_rejected(self):
        self.reject(lambda d:d["snapshot"]["controls"].pop())

    def test_acceptance_count_modified_rejected(self):
        self.reject(lambda d:d["snapshot"]["counts"].update(accepted=1))

    def test_immutable_lock_blob_changed_rejected(self):
        self.reject(lambda d:d["snapshot"].update(baseline_increment7_lock_blob_sha="0"*40))

    def test_prior_ci_provenance_forged_rejected(self):
        self.reject(lambda d:d["snapshot"]["previously_tested_ci"].update(conclusion="CERTIFIED"))

    def test_unauthorized_runtime_path_rejected(self):
        s=good_state();s["changed_paths"].append("backend/app/main.py")
        self.reject(lambda d:None,state=s)

    def test_missing_snapshot_document_rejected(self):
        s=good_state();s["changed_paths"].pop()
        self.reject(lambda d:None,state=s)

    def test_main_moved_rejected(self):
        s=good_state();s["main"]="9"*40
        self.reject(lambda d:None,state=s)

    def test_wrong_branch_rejected(self):
        s=good_state();s["branch"]="main"
        self.reject(lambda d:None,state=s)

    def test_dirty_checkout_rejected(self):
        s=good_state();s["working_tree_clean"]=False
        self.reject(lambda d:None,state=s)

    def test_wrong_baseline_git_blob_rejected(self):
        s=good_state();s["historical_lock_blob"]="1"*40
        self.reject(lambda d:None,state=s)

    def test_symlink_mode_rejected(self):
        s=good_state();s["file_modes"][PATHS[1]]="120000"
        self.reject(lambda d:None,state=s)

    def test_missing_no_go_document_rejected(self):
        self.reject(lambda d:None,doc="OFFLINE VALIDATION: PASS")

    def test_forged_go_statement_in_document_rejected(self):
        from scripts.verify_increment8_closeout import DOCUMENT
        s=DOCUMENT.read_text(encoding="utf-8")+"\nPRODUCTION DEPLOYMENT: GO\n"
        self.reject(lambda d:None,doc=s)

    def test_historical_control_lock_tamper_rejected(self):
        self.reject(lambda d:d["prior"]["lock"]["source_entries"][0].update(git_blob_sha1="f"*40))


if __name__ == "__main__":
    unittest.main()
