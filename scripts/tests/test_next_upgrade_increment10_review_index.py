"""No broker or production I/O: fail-closed final review index tests."""
import copy
import unittest
from scripts.verify_increment10_review_index import ALLOW,BRANCH,TREE,MAIN,FILES,ROOT,verify,load_package,DOCUMENT

def checkout():
    return {"branch":BRANCH,"head":"a"*40,"baseline_tree":TREE,"main":MAIN,
            "changed":list(ALLOW),"clean":True,"file_modes":dict.fromkeys(ALLOW,"100644")}

class FinalReviewIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot=load_package()
    def reject(self,mutation,**extra):
        d=copy.deepcopy(self.snapshot)
        mutation(d)
        params={"state":checkout()}
        params.update(extra)
        with self.assertRaises((ValueError,KeyError,TypeError)):
            verify(d,**params)
    def test_positive_is_source_only_and_no_go(self):
        r=verify(copy.deepcopy(self.snapshot),state=checkout())
        self.assertEqual((r["accepted"],r["blocked"]),(0,12))
        self.assertEqual(r["increment9_pinned_files"],4)
        self.assertEqual(r["branch_hygiene"],"PASS")
        self.assertEqual(r["production_deployment"],"NO-GO")
    def test_fake_owner_rejected(self):
        self.reject(lambda d:d["index"]["controls"][0].update(human_owner_confirmed=True))
    def test_fake_reviewer_rejected(self):
        self.reject(lambda d:d["index"]["controls"][1].update(independent_reviewer_confirmed=True))
    def test_fake_acceptance_rejected(self):
        self.reject(lambda d:d["index"]["controls"][2].update(accepted=True))
    def test_fake_real_receipt_rejected(self):
        self.reject(lambda d:d["index"]["controls"][3].update(real_evidence_receipts=1))
    def test_missing_control_rejected(self):
        self.reject(lambda d:d["index"]["controls"].pop())
    def test_forged_go_rejected(self):
        self.reject(lambda d:d["index"]["decisions"].update({"PRODUCTION DEPLOYMENT":"GO"}))
    def test_main_merge_claim_rejected(self):
        self.reject(lambda d:d["index"]["counts"].update(main_merges=1))
    def test_fake_signoff_rejected(self):
        self.reject(lambda d:d["index"]["review_gates"].update(security_signoff="PROVIDED"))
    def test_wrong_pinned_source_id_rejected(self):
        self.reject(lambda d:d["index"]["locked_increment9_sources"][0].update(git_blob_sha="0"*40))
    def test_altered_source_bytes_rejected(self):
        def read(p):
            raw=(ROOT/p).read_bytes()
            return raw+b"mutation" if p==FILES[0]["path"] else raw
        self.reject(lambda d:None,reader=read)
    def test_unexpected_runtime_diff_rejected(self):
        s=checkout();s["changed"].append("backend/app/main.py")
        self.reject(lambda d:None,state=s)
    def test_omitted_evidence_file_rejected(self):
        s=checkout();s["changed"].pop()
        self.reject(lambda d:None,state=s)
    def test_dirty_worktree_rejected(self):
        s=checkout();s["clean"]=False
        self.reject(lambda d:None,state=s)
    def test_changed_main_ref_rejected(self):
        s=checkout();s["main"]="f"*40
        self.reject(lambda d:None,state=s)
    def test_broadened_file_allowlist_rejected(self):
        self.reject(lambda d:d["index"]["increment10_exact_change_paths"].append("backend/new.py"))
    def test_bad_mode_rejected(self):
        s=checkout();s["file_modes"][ALLOW[1]]="120000"
        self.reject(lambda d:None,state=s)
    def test_fabricated_prior_ci_rejected(self):
        self.reject(lambda d:d["index"]["validated_increment9_ci"].update(result="CERTIFIED"))
    def test_forged_operational_text_rejected(self):
        self.reject(lambda d:None,document=DOCUMENT.read_text()+"\nPRODUCTION DEPLOYMENT: GO\n")
    def test_missing_freeze_document_rejected(self):
        self.reject(lambda d:None,document="OFFLINE VALIDATION: PASS")

if __name__=="__main__":
    unittest.main()
