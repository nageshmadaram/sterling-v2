"""Negative regression tests: frozen source integrity is not a release approval."""
import copy
import unittest

from scripts.verify_increment9_review_freeze import (
    ALLOW, BRANCH, TREE, MAIN, DOCUMENT, load_package, verify,
)


def state():
    return {"branch":BRANCH,"head":"a"*40,"baseline_tree":TREE,"main":MAIN,
            "working_tree_clean":True,"changed_paths":list(ALLOW),
            "file_modes":dict.fromkeys(ALLOW,"100644")}


class IncrementNineFrozenReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.good = load_package()

    def reject(self, modify, **options):
        d=copy.deepcopy(self.good)
        modify(d)
        controls={"state":state()}
        controls.update(options)
        with self.assertRaises((ValueError,KeyError,TypeError)):
            verify(d,**controls)

    def test_positive_review_has_zero_authority(self):
        v=verify(copy.deepcopy(self.good),state=state())
        self.assertEqual((v["controls_accepted"],v["controls_blocked"]),(0,12))
        self.assertEqual(v["increment8_pinned_files_verified"],4)
        self.assertEqual(v["baseline_historical_sources_verified"],26)
        self.assertEqual(v["main_freeze"],"PASS")
        self.assertEqual(v["production_deployment"],"NO-GO")

    def test_fake_owner(self):
        self.reject(lambda d:d["snapshot"]["controls"][0].update(owner_appointed=True))

    def test_fake_independent_reviewer(self):
        self.reject(lambda d:d["snapshot"]["controls"][1].update(reviewer_appointed=True))

    def test_fake_signed_control(self):
        self.reject(lambda d:d["snapshot"]["controls"][2].update(accepted=True))

    def test_fabricated_receipt(self):
        self.reject(lambda d:d["snapshot"]["controls"][3].update(independent_receipts=1))

    def test_missing_control(self):
        self.reject(lambda d:d["snapshot"]["controls"].pop())

    def test_false_go(self):
        self.reject(lambda d:d["snapshot"]["decision"].update({"PRODUCTION DEPLOYMENT":"GO"}))

    def test_live_order_authority(self):
        self.reject(lambda d:d["snapshot"]["actions"].update(route_orders=True))

    def test_fake_release_signoff(self):
        self.reject(lambda d:d["snapshot"]["governance"].update(independent_signoffs=1))

    def test_fake_historical_ci(self):
        self.reject(lambda d:d["snapshot"]["increment8_ci"].update(conclusion="CERTIFIED"))

    def test_source_pin_forgery(self):
        self.reject(lambda d:d["snapshot"]["historical_files"][0].update(git_blob_sha1="f"*40))

    def test_source_bytes_mutated(self):
        from scripts.verify_increment9_review_freeze import FILES
        def read(p):
            from scripts.verify_increment9_review_freeze import ROOT
            raw=(ROOT/p).read_bytes()
            return raw+b"mutation" if p==FILES[0][0] else raw
        self.reject(lambda d:None,source_reader=read)

    def test_unexpected_runtime_file_diff(self):
        s=state();s["changed_paths"].append("backend/app/main.py")
        self.reject(lambda d:None,state=s)

    def test_missing_evidence_file(self):
        s=state();s["changed_paths"].pop()
        self.reject(lambda d:None,state=s)

    def test_dirty_checkout(self):
        s=state();s["working_tree_clean"]=False
        self.reject(lambda d:None,state=s)

    def test_main_modified(self):
        s=state();s["main"]="f"*40
        self.reject(lambda d:None,state=s)

    def test_wrong_base_tree(self):
        s=state();s["baseline_tree"]="0"*40
        self.reject(lambda d:None,state=s)

    def test_symlink_or_wrong_mode(self):
        s=state();s["file_modes"][ALLOW[1]]="120000"
        self.reject(lambda d:None,state=s)

    def test_broadened_allowlist(self):
        self.reject(lambda d:d["snapshot"]["change_allowlist"].append("backend/app/main.py"))

    def test_pretend_historical_evidence_is_production(self):
        self.reject(lambda d:d["snapshot"].update(scope="CERTIFIED_PRODUCTION_RELEASE"))

    def test_final_document_forged_go(self):
        self.reject(lambda d:None, document=DOCUMENT.read_text()+"\nPRODUCTION DEPLOYMENT: GO\n")

    def test_missing_final_document(self):
        self.reject(lambda d:None,document="OFFLINE VALIDATION: PASS")


if __name__ == "__main__":
    unittest.main()
