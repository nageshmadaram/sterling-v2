"""Exact-commit evidence generation is never production authorization."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.preproduction_finish_review import build_manifest, main

ROOT = Path(__file__).resolve().parents[2]
HEAD = "a" * 40


class FinishReviewTests(unittest.TestCase):
    def test_manifest_has_correct_source_provenance_but_no_approval(self):
        artifact = build_manifest(ROOT, HEAD, run_id="12345")
        self.assertEqual(artifact["commit_sha"], HEAD)
        self.assertEqual(artifact["github_actions_run_id"], "12345")
        self.assertEqual(artifact["decision"], "NO_GO")
        self.assertFalse(artifact["technical_review_complete"])
        self.assertFalse(artifact["production_certified"])
        self.assertFalse(artifact["broker_order_routing_authorized"])
        self.assertFalse(artifact["live_trading_authorized"])
        self.assertEqual((artifact["accepted_controls"], artifact["blocked_controls"]), (0, 12))
        self.assertEqual(len(artifact["source_registry_hashes"]), 2)
        self.assertEqual(len(artifact["controls"]), 12)
        self.assertTrue(all(
            x["technical_status"] == "NOT_INDEPENDENTLY_VERIFIED"
            and x["independent_signoff"] == "NOT_PROVIDED"
            and x["external_evidence"] == "NOT_SUBMITTED"
            for x in artifact["controls"]
        ))
        self.assertNotIn("api_key", json.dumps(artifact).lower())

    def test_bad_sha_or_run_id_cannot_bind_unverified_proof(self):
        for sha in ("", "deadbeef", "A" * 40, "g" * 40, HEAD + "a"):
            with self.subTest(sha=sha):
                with self.assertRaises(ValueError):
                    build_manifest(ROOT, sha)
        for run_id in ("0", "-5", "NaN", "12x"):
            with self.subTest(run_id=run_id):
                with self.assertRaises(ValueError):
                    build_manifest(ROOT, HEAD, run_id=run_id)

    def test_modified_go_registry_is_rejected_even_if_sha_is_valid(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ops = root / "docs/ops"
            ops.mkdir(parents=True)
            controls = json.loads((ROOT / "docs/ops/PREPRODUCTION_CONTROLS.json").read_text())
            evidence = json.loads((ROOT / "docs/ops/PREPRODUCTION_EVIDENCE_REGISTRY.json").read_text())
            controls["decision"] = "GO"
            controls["acceptance_count"] = 12
            controls["blocked_count"] = 0
            controls["controls"][0]["current_status"] = "ACCEPTED"
            (ops / "PREPRODUCTION_CONTROLS.json").write_text(json.dumps(controls))
            (ops / "PREPRODUCTION_EVIDENCE_REGISTRY.json").write_text(json.dumps(evidence))
            with self.assertRaises(ValueError):
                build_manifest(root, HEAD)

    def test_cli_produces_atomic_review_only_manifest(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "evidence.json"
            status = main(["--root", str(ROOT), "--head-sha", HEAD,
                           "--run-id", "7", "--output", str(target)])
            self.assertEqual(status, 0)
            output = json.loads(target.read_text())
            self.assertEqual(output["decision"], "NO_GO")
            self.assertEqual(output["commit_sha"], HEAD)
            self.assertEqual(output["github_actions_run_id"], "7")
            self.assertFalse((target.with_name("evidence.json.tmp")).exists())

    def test_cli_failure_does_not_publish_a_success_artifact(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "review.json"
            status = main(["--root", str(ROOT), "--head-sha", "not-a-sha",
                           "--output", str(target)])
            self.assertNotEqual(status, 0)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
