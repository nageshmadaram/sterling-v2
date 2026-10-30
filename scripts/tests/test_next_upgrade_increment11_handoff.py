"""Negative tests for final source-only governance handoff, without I/O to providers."""
import copy
import unittest

from scripts.verify_increment11_handoff import (
    BASE, BRANCH, MAIN, TREE, ALLOW, FILES, ROOT, DOCUMENT, verify, load_package,
)


def checkout():
    return {"branch":BRANCH,"head":BASE,"base_tree":TREE,"main_sha":MAIN,
            "clean":True,"changed_files":list(ALLOW),"modes":dict.fromkeys(ALLOW,"100644")}


class FinalGovernanceHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = load_package()

    def reject(self, mutate, **options):
        obj = copy.deepcopy(self.package)
        mutate(obj)
        kwargs = {"state":checkout()}
        kwargs.update(options)
        with self.assertRaises((KeyError, ValueError, TypeError)):
            verify(obj, **kwargs)

    def test_valid_review_stays_no_go(self):
        result=verify(copy.deepcopy(self.package), state=checkout())
        self.assertEqual((result["accepted"],result["blocked"]),(0,12))
        self.assertEqual(result["historic_increment10_sources_verified"],4)
        self.assertEqual(result["branch_hygiene"],"PASS")
        self.assertEqual(result["main_snapshot"],"UNCHANGED")
        self.assertEqual(result["production_deployment"],"NO-GO")

    def test_fake_owner_refused(self):
        self.reject(lambda x:x["handoff"]["production_controls"][0].update(owner_confirmed=True))

    def test_fake_reviewer_refused(self):
        self.reject(lambda x:x["handoff"]["production_controls"][1].update(reviewer_confirmed=True))

    def test_fake_control_receipt_refused(self):
        self.reject(lambda x:x["handoff"]["production_controls"][2].update(receipts_accepted=1))

    def test_control_missing_refused(self):
        self.reject(lambda x:x["handoff"]["production_controls"].pop())

    def test_control_order_changed_refused(self):
        self.reject(lambda x:x["handoff"]["production_controls"].reverse())

    def test_fake_human_signoff_refused(self):
        self.reject(lambda x:x["handoff"]["human_review"].update(security_signoff="SIGNED"))

    def test_fake_go_refused(self):
        self.reject(lambda x:x["handoff"]["decisions"].update({"PRODUCTION DEPLOYMENT":"GO"}))

    def test_fake_broker_authority_refused(self):
        self.reject(lambda x:x["handoff"]["operations"].update(broker_routing=True))

    def test_fake_ci_evidence_refused(self):
        self.reject(lambda x:x["handoff"]["ci_reference"].update(result="PRODUCTION_CERTIFIED"))

    def test_source_hash_changed_refused(self):
        self.reject(lambda x:x["handoff"]["pinned_sources"][0].update(git_blob_sha1="f"*40))

    def test_historical_source_byte_mutation_refused(self):
        def modified(p):
            raw = (ROOT/p).read_bytes()
            return raw+b"tamper" if p==FILES[0][0] else raw
        self.reject(lambda x:None,read_source=modified)

    def test_unexpected_backend_file_refused(self):
        state=checkout();state["changed_files"].append("backend/app/main.py")
        self.reject(lambda x:None,state=state)

    def test_missing_review_file_refused(self):
        state=checkout();state["changed_files"].pop()
        self.reject(lambda x:None,state=state)

    def test_main_moved_refused(self):
        state=checkout();state["main_sha"]="a"*40
        self.reject(lambda x:None,state=state)

    def test_dirty_worktree_refused(self):
        state=checkout();state["clean"]=False
        self.reject(lambda x:None,state=state)

    def test_wrong_file_mode_refused(self):
        state=checkout();state["modes"][ALLOW[0]]="120000"
        self.reject(lambda x:None,state=state)

    def test_allowlist_broadened_refused(self):
        self.reject(lambda x:x["handoff"]["allowed_increment11_paths"].append("backend/app/main.py"))

    def test_fake_production_document_refused(self):
        self.reject(lambda x:None,document=DOCUMENT.read_text()+"\nPRODUCTION DEPLOYMENT: GO\n")

    def test_missing_freeze_sentence_refused(self):
        self.reject(lambda x:None,document="OFFLINE VALIDATION: PASS")


if __name__ == "__main__":
    unittest.main()
