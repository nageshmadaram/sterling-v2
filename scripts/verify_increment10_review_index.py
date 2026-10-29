#!/usr/bin/env python3
"""Read-only evidence review index. A PASS never grants production authority."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from scripts.verify_increment9_review_freeze import load_package as load_previous, verify as verify_previous

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/evidence/2026-10-29-next-upgrade/increment-10-final-readonly-review-index.json"
DOCUMENT = ROOT / "docs/implementation/2026-10-29-final-review-index-increment-10.md"
BRANCH = "upgrade/evidence-traceability-20261029"
BASE = "831e735cb9fb1c4a21974b050db2a5c04604593e"
TREE = "f9d14afb38395b416c969c74b94b2ca086116f85"
MAIN = "27e39e954ee67dbd5ec1d68ab9d15ff15de7dd7f"
FILES = [{"path":"docs/evidence/2026-10-29-next-upgrade/increment-9-final-review-freeze.json","git_blob_sha":"ab08f4aab6744fc242ce2a1224f42d88cf53ab72","classification":"GIT_SOURCE_ONLY_NOT_PRODUCTION_EVIDENCE"},{"path":"docs/implementation/2026-10-29-final-review-closure-increment-9.md","git_blob_sha":"8dc415f268615d766466ae9cb54b96e13de70083","classification":"GIT_SOURCE_ONLY_NOT_PRODUCTION_EVIDENCE"},{"path":"scripts/verify_increment9_review_freeze.py","git_blob_sha":"ce254bcacf8a952fa39ea1cd3d1033c4a380bf63","classification":"GIT_SOURCE_ONLY_NOT_PRODUCTION_EVIDENCE"},{"path":"scripts/tests/test_next_upgrade_increment9_review_freeze.py","git_blob_sha":"814a66651d81108c14f83906f7818a8662551e4d","classification":"GIT_SOURCE_ONLY_NOT_PRODUCTION_EVIDENCE"}]
ALLOW = [".github/workflows/next-upgrade-evidence.yml","docs/evidence/2026-10-29-next-upgrade/increment-10-final-readonly-review-index.json","docs/implementation/2026-10-29-final-review-index-increment-10.md","scripts/verify_increment10_review_index.py","scripts/tests/test_next_upgrade_increment10_review_index.py"]
IDS = ("H01","T02","S03","I04","D05","R06","B07","M08","A09","L10","F11","G12")
DECISIONS = {
    "OFFLINE VALIDATION":"PASS", "PRODUCTION DEPLOYMENT":"NO-GO",
    "LIVE TRADING":"NOT AUTHORIZED", "BROKER ORDER ROUTING":"NOT AUTHORIZED",
    "production_certification":"NOT ACHIEVED", "release_freeze":"ACTIVE",
    "independent_release_signoff":"NOT PROVIDED", "main_merge":"NOT AUTHORIZED",
}
FINAL = "Production certification remains not achieved. Release freeze remains active."

def demand(value, issue):
    if not value:
        raise ValueError(issue)

def git(*args):
    return subprocess.check_output(["git",*args],cwd=ROOT,stderr=subprocess.DEVNULL).decode().strip()

def read_checkout():
    """No network, fetch, secrets, operational environment or broker I/O."""
    branch = os.getenv("GITHUB_REF_NAME") or git("branch","--show-current")
    head = git("rev-parse","HEAD")
    demand(branch == BRANCH and git("merge-base",BASE,head) == BASE, "unexpected checkout")
    if os.getenv("GITHUB_SHA"):
        demand(os.environ["GITHUB_SHA"] == head, "wrong CI event SHA")
    changed = subprocess.check_output(["git","diff","--no-renames","--name-only","-z",BASE,head],
                                      cwd=ROOT,stderr=subprocess.DEVNULL)
    return {
        "branch":branch,"head":head,"baseline_tree":git("rev-parse",BASE+"^{tree}"),
        "main":git("rev-parse","refs/remotes/origin/main"),
        "changed":[os.fsdecode(x) for x in changed.split(b"\0") if x],
        "clean":subprocess.check_output(["git","status","--porcelain","-z"],cwd=ROOT)==b"",
        "file_modes":{p: (git("ls-files","--stage","--",p).split(" ",1)[0] or None) for p in ALLOW},
    }

def load_package():
    return {"index":json.loads(MANIFEST.read_text(encoding="utf-8")),"previous":load_previous()}

def verify(data, *, state=None, document=None, reader=None):
    m = data["index"]
    prev = verify_previous(data["previous"])
    demand(prev["validation"]=="PASS" and prev["controls_accepted"]==0
           and prev["baseline_historical_sources_verified"]==26
           and prev["increment8_pinned_files_verified"]==4,
           "previous independent source consistency not retained")
    demand(m["schema"]=="sterling.v2.final_readonly_review_index.v1"
           and m["type"]=="SOURCE_ONLY_NONOPERATIONAL_REVIEW_INDEX",
           "unexpected schema or release record")
    demand(m["repo"]=="nageshmadaram/sterling-v2" and m["branch"]==BRANCH
           and m["baseline_increment9_sha"]==BASE and m["baseline_increment9_tree"]==TREE
           and m["observed_main_sha"]==MAIN,"source refs changed")
    demand(m["validated_increment9_ci"] == {
        "run":40920065878,"result":"SUCCESS","jobs_passed":10,
        "source_only_tests_passed":144,"disposable_backend_tests":66,
        "mocked_frontend_tests":21,"synthetic_browser_tests":6,"physical_device_tests":0,
    }, "historical CI facts changed")
    demand(m["historical_increment9_checkout"]==
           "PIN_TO_831e735cb9fb1c4a21974b050db2a5c04604593e",
           "historical replay no longer pinned")
    demand(m["increment10_exact_change_paths"]==ALLOW, "review allowlist changed")
    demand(m["review_scope"]=="ENGINEERING_EVIDENCE_ONLY_NO_INDEPENDENT_PRODUCTION_ACCEPTANCE",
           "unsupported approval classification")
    demand(m["decisions"]==DECISIONS and m["closing_sentence"]==FINAL,
           "protected NO-GO decision changed")
    demand(m["counts"] == {
        "production_controls_accepted":0,"production_controls_blocked":12,
        "independent_production_receipts":0,"human_signatures":0,
        "production_deployments":0,"live_broker_connections":0,"main_merges":0
    }, "unsupported receipt or operational actions")
    demand(m["review_gates"]=={
        "human_appointments":"NOT CONFIRMED","h01_tooling":"PASS","h01_real_host":"BLOCKED",
        "security_signoff":"NOT PROVIDED","operations_signoff":"NOT PROVIDED",
        "release_governance_signoff":"NOT PROVIDED","production_certification":"NOT ACHIEVED",
        "release_freeze":"ACTIVE",
    }, "review gates incorrectly promoted")
    demand([x["id"] for x in m["controls"]]==list(IDS),"controls incomplete")
    for x in m["controls"]:
        demand(x=={
            "id":x["id"],"status":"BLOCKED","accepted":False,
            "human_owner_confirmed":False,"independent_reviewer_confirmed":False,
            "real_evidence_receipts":0,"independent_signoff":"NOT PROVIDED",
        }, "control acceptance unsupported")
    demand(m["source_integrity_warning"] ==
           "Git and SHA-256 digests validate repository content only; CI is not independent evidence of operational readiness or production authority.",
           "source hashes promoted to operational approval")
    demand(len(m["prior_sources"])==5 and all((ROOT/path).is_file() for path in m["prior_sources"].values()),
           "historical evidence references missing")
    demand(m["next_review_gate"].startswith("Separate authorized humans"),"missing independent future gate")
    demand(m["locked_increment9_sources"]==FILES, "historical file lock diverged")
    sha256={}
    for f in FILES:
        path=ROOT/f["path"]
        demand(not path.is_symlink() and path.is_file() and
               path.resolve().is_relative_to(ROOT.resolve()) and path.stat().st_size < 1_000_000,
               "missing or unsafe historical file")
        raw=reader(f["path"]) if reader is not None else path.read_bytes()
        demand(isinstance(raw,bytes) and len(raw)<1_000_000,"invalid file bytes")
        git_hash=hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()
        demand(git_hash==f["git_blob_sha"],"historical source mutated: "+f["path"])
        sha256[f["path"]]=hashlib.sha256(raw).hexdigest()
    if state is not None:
        demand(state["branch"]==BRANCH and re.fullmatch("[a-f0-9]{40}",state["head"]),
               "wrong branch or HEAD")
        demand(state["baseline_tree"]==TREE and state["main"]==MAIN,"main or source tree changed")
        demand(state["clean"] is True,"uncommitted changes")
        changed=state["changed"]
        demand(len(changed)==len(set(changed)) and set(changed)==set(ALLOW),
               "runtime or unapproved file diff, or omitted review file")
        demand(state["file_modes"]==dict.fromkeys(ALLOW,"100644"),"unexpected file type or mode")
    if document is None:
        document=DOCUMENT.read_text(encoding="utf-8")
    for phrase in ("OFFLINE VALIDATION: PASS","PRODUCTION DEPLOYMENT: NO-GO",
                   "LIVE TRADING: NOT AUTHORIZED","BROKER ORDER ROUTING: NOT AUTHORIZED",
                   "0/12","12/12","not production evidence","human",
                   "release freeze","source-only",FINAL):
        demand(phrase.lower() in document.lower(),"missing document boundary: "+phrase)
    for bad in (r"PRODUCTION DEPLOYMENT:\s*GO\b",r"LIVE TRADING:\s*AUTHORIZED\b",
                r"BROKER ORDER ROUTING:\s*AUTHORIZED\b",r"PRODUCTION CERTIFICATION:\s*ACHIEVED\b"):
        demand(re.search(bad,document,re.I) is None,"unsupported GO claim in report")
    return {
        "validation":"PASS","scope":"READ_ONLY_REVIEW_NOT_OPERATIONAL_AUTHORITY",
        "prior_historical_sources":26,"increment8_pinned_files":4,
        "increment9_pinned_files":len(sha256),"sha256_of_repository_sources":sha256,
        "branch_hygiene":"PASS" if state else "NOT_CHECKED",
        "main_frozen":"PASS" if state else "NOT_CHECKED",
        "accepted":0,"blocked":12,"human_appointments":0,"independent_receipts":0,
        "independent_signoffs":0,"production_certification":"NOT ACHIEVED",
        "release_freeze":"ACTIVE","offline_validation":"PASS",
        "production_deployment":"NO-GO","live_trading":"NOT AUTHORIZED",
        "broker_order_routing":"NOT AUTHORIZED",
    }

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path)
    args=p.parse_args()
    try:
        report=verify(load_package(),state=read_checkout())
        if args.output:
            args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    except (OSError,ValueError,TypeError,KeyError,subprocess.CalledProcessError) as e:
        print("FINAL REVIEW INDEX BLOCKED: "+str(e))
        return 1
    print("INCREMENT10 SOURCE-ONLY PASS: 12/12 blocked; NO-GO. No independent approval.")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
