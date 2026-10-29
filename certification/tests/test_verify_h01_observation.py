"""Reject forged H01 acceptance, unsafe files, and structurally invalid evidence."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from certification import observe_host_h01 as collector
from certification import verify_h01_observation as verifier


class H01ObservationValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (
            patch.object(collector, "os_release", return_value={
                "id": "ubuntu", "version_id": "26.04",
            }),
            patch.object(collector, "status_bits", return_value={
                "no_new_privileges": True,
                "seccomp_mode": 2,
                "effective_caps_empty": True,
                "running_as_root": False,
            }),
            patch.object(collector, "kernel_isolation", return_value={
                "apparmor_module_enabled": True,
                "selinux_enforcing": None,
                "user_namespace_mapping_present": False,
            }),
            patch.object(collector.platform, "release", return_value="7.0.0-example"),
        ):
            cls.sample = collector.build_observation("env-001", "approval-001")

    def test_compatible_collector_schema_never_certifies(self):
        self.assertEqual(
            [], verifier.validate_observation(self.sample, "env-001", "approval-001")
        )
        self.assertEqual("NOT_VERIFIED", self.sample["control_acceptance"])

    def test_reject_false_production_acceptance(self):
        variants = (
            ("control_acceptance", "PASS"),
            ("production_security_certified", True),
            ("production_deployment", "GO"),
            ("live_trading", "AUTHORIZED"),
            ("broker_order_routing", "AUTHORIZED"),
            ("independent_reviewer", "self-signed"),
            ("independent_approval", True),
            ("network_segmentation_verified", True),
            ("runtime_boundary_verified", True),
            ("candidate_commit", "fake"),
        )
        for key, value in variants:
            with self.subTest(key=key):
                item = copy.deepcopy(self.sample)
                item[key] = value
                self.assertTrue(verifier.validate_observation(item, "env-001", "approval-001"))

    def test_wrong_environment_or_approval_rejected(self):
        self.assertTrue(verifier.validate_observation(self.sample, "env-OTHER", "approval-001"))
        self.assertTrue(verifier.validate_observation(self.sample, "env-001", "approval-OTHER"))

    def test_unexpected_personal_or_sensitive_data_is_rejected(self):
        item = copy.deepcopy(self.sample)
        item["observed_facts"]["access_token"] = "sensitive"
        self.assertTrue(verifier.validate_observation(item, "env-001", "approval-001"))
        item = copy.deepcopy(self.sample)
        item["operator_email"] = "private@example.com"
        self.assertTrue(verifier.validate_observation(item, "env-001", "approval-001"))
        item = copy.deepcopy(self.sample)
        item["observed_facts"]["kernel_release"] = "evil\nTOKEN=secret"
        self.assertTrue(verifier.validate_observation(item, "env-001", "approval-001"))

    def test_boolean_and_kernel_types_are_strict(self):
        item = copy.deepcopy(self.sample)
        item["observed_facts"]["process_security"]["seccomp_mode"] = True
        self.assertTrue(verifier.validate_observation(item, "env-001", "approval-001"))
        item = copy.deepcopy(self.sample)
        item["observed_facts"]["kernel_isolation_signals"]["selinux_enforcing"] = 1
        self.assertTrue(verifier.validate_observation(item, "env-001", "approval-001"))

    def test_malformed_timestamps_rejected(self):
        for stamp in ("2026-10-29", "2026-10-29T08:00:00", "not-a-time"):
            with self.subTest(timestamp=stamp):
                item = copy.deepcopy(self.sample)
                item["observed_at_utc"] = stamp
                self.assertTrue(verifier.validate_observation(item, "env-001", "approval-001"))

    def test_bounded_private_file_digest(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "observation.json"
            collector.write_new_private_file(target, self.sample)
            observation, digest = verifier.read_private_observation(target)
            self.assertEqual(self.sample, observation)
            self.assertEqual(64, len(digest))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual([], verifier.validate_observation(observation, "env-001", "approval-001"))

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "observation.json"
            target.write_text('{"schema":"good","schema":"bad"}', encoding="utf-8")
            target.chmod(0o600)
            with self.assertRaises(ValueError):
                verifier.read_private_observation(target)

    def test_world_readable_file_refused(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "observation.json"
            target.write_text("{}", encoding="utf-8")
            target.chmod(0o644)
            with self.assertRaises(ValueError):
                verifier.read_private_observation(target)

    def test_symlink_and_directory_refused(self):
        with tempfile.TemporaryDirectory() as td:
            original = Path(td) / "real"
            original.write_text("{}", encoding="utf-8")
            original.chmod(0o600)
            link = Path(td) / "symlink"
            link.symlink_to(original)
            with self.assertRaises(ValueError):
                verifier.read_private_observation(link)
            with self.assertRaises(ValueError):
                verifier.read_private_observation(Path(td))

    def test_large_file_refused(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "observation.json"
            target.write_bytes(b"a" * (verifier.MAX_FILE_BYTES + 1))
            target.chmod(0o600)
            with self.assertRaises(ValueError):
                verifier.read_private_observation(target)

    def test_cli_pass_is_explicitly_non_authoritative(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "observation.json"
            collector.write_new_private_file(target, self.sample)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = verifier.main([
                    "--observation", str(target),
                    "--environment-id", "env-001",
                    "--authorization-ref", "approval-001",
                ])
            record = json.loads(output.getvalue())
            self.assertEqual(0, rc)
            self.assertEqual("PASS", record["structural_validation"])
            self.assertFalse(record["independently_verified"])
            self.assertEqual("BLOCKED", record["production_certification"])
            self.assertEqual("NO_GO", record["production_deployment"])
            self.assertEqual("NOT_AUTHORIZED", record["live_trading"])
            self.assertNotIn("kernel_release", record)

    def test_cli_fails_closed_without_leaking_host_details(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "observation.json"
            collector.write_new_private_file(target, self.sample)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = verifier.main([
                    "--observation", str(target),
                    "--environment-id", "wrong-env",
                    "--authorization-ref", "approval-001",
                ])
            record = json.loads(output.getvalue())
            self.assertEqual(2, rc)
            self.assertEqual("FAIL", record["structural_validation"])
            self.assertIsNone(record["file_sha256"])
            self.assertEqual("BLOCKED", record["control_acceptance"])

    def test_not_an_external_probe_or_broker_client(self):
        import inspect
        source = inspect.getsource(verifier)
        for forbidden in (
            "subprocess.", "requests.", "urllib.request",
            "socket.", "os.system(", "kubectl ", "docker ",
            "kite.", "broker.login", "ssl.SSLSocket",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
