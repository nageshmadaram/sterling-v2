"""Contract tests for the opt-in H01 observer. Never touch an actual host."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from certification import observe_host_h01 as h


class HostObservationTests(unittest.TestCase):
    def test_fixed_keys_never_claim_certification(self):
        with (
            patch.object(h, "os_release", return_value={"id": "ubuntu", "version_id": "26.04"}),
            patch.object(h, "status_bits", return_value={"running_as_root": False}),
            patch.object(h, "kernel_isolation", return_value={"apparmor_module_enabled": True}),
            patch.object(h.platform, "release", return_value="example-kernel"),
        ):
            report = h.build_observation("env-001", "approval-001")
        self.assertEqual("H01", report["control_id"])
        self.assertEqual("NOT_VERIFIED", report["control_acceptance"])
        self.assertEqual("NO_GO", report["production_deployment"])
        self.assertEqual("NOT_AUTHORIZED", report["broker_order_routing"])
        self.assertEqual("NOT_AUTHORIZED", report["live_trading"])
        self.assertFalse(report["runtime_boundary_verified"])
        self.assertFalse(report["network_segmentation_verified"])
        self.assertFalse(report["production_security_certified"])
        self.assertIsNone(report["independent_approval"])
        self.assertIsNone(report["independent_reviewer"])

    def test_opaque_refs_refuse_secrets_and_path_injection(self):
        for invalid in ("", "ab", "../../etc", "my secret", "$SECRET", "a" * 120):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    h.build_observation(invalid, "approval-001")

    def test_status_is_conservative_for_missing_data(self):
        with patch.object(h, "read_text", return_value=None):
            flags = h.status_bits()
        self.assertIsNone(flags["no_new_privileges"])
        self.assertIsNone(flags["seccomp_mode"])
        self.assertIsNone(flags["effective_caps_empty"])

    def test_status_parses_only_known_kernel_fields(self):
        sample = (
            "Name:\tpython\n"
            "Uid:\t1000 1000 1000 1000\n"
            "NoNewPrivs:\t1\nSeccomp:\t2\nCapEff:\t0000000000000000\n"
            "BrokerToken:\tSHOULD_NOT_APPEAR\n"
        )
        with patch.object(h, "read_text", return_value=sample):
            flags = h.status_bits()
        self.assertTrue(flags["no_new_privileges"])
        self.assertEqual(2, flags["seccomp_mode"])
        self.assertTrue(flags["effective_caps_empty"])
        self.assertNotIn("BrokerToken", flags)

    def test_kernel_observation_is_only_a_signal(self):
        records = {
            "/sys/module/apparmor/parameters/enabled": "Y\n",
            "/sys/fs/selinux/enforce": "1\n",
            "/proc/self/uid_map": "0 100000 65536\n",
        }
        with patch.object(h, "read_text", side_effect=lambda p: records.get(str(p))):
            flags = h.kernel_isolation()
        self.assertTrue(flags["apparmor_module_enabled"])
        self.assertTrue(flags["selinux_enforcing"])
        self.assertTrue(flags["user_namespace_mapping_present"])

    def test_restricted_output_and_immutable_creation(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "host-observation.json"
            record = {"control_acceptance": "NOT_VERIFIED"}
            h.write_new_private_file(p, record)
            self.assertEqual(record, json.loads(p.read_text()))
            self.assertEqual(0o600, stat.S_IMODE(p.stat().st_mode))
            with self.assertRaises(FileExistsError):
                h.write_new_private_file(p, record)

    def test_symlink_output_refused(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "untouched"
            target.write_text("safe", encoding="utf-8")
            link = Path(td) / "symlink"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                h.write_new_private_file(link, {"x": "safe"})
            self.assertEqual("safe", target.read_text())

    def test_relative_output_refused(self):
        with self.assertRaises(ValueError):
            h.write_new_private_file(Path("relative.json"), {"x": 1})

    def test_no_external_commands_or_network(self):
        import inspect
        source = inspect.getsource(h)
        for forbidden in (
            "subprocess.", "requests.", "http.client", "socket.",
            "os.system(", "os.popen(", "docker ", "kubectl ",
            "broker.login", "kite.", "postgresql://",
        ):
            self.assertNotIn(forbidden, source)

    def test_requires_explicit_approval_and_unprivileged_execution(self):
        with patch.object(h.os, "geteuid", return_value=1000):
            with self.assertRaises(SystemExit):
                h.main([
                    "--environment-id", "env-001",
                    "--authorization-ref", "approval-001",
                    "--output", "/tmp/should-not-exist-h01.json",
                ])
        with patch.object(h.os, "geteuid", return_value=0):
            with self.assertRaises(SystemExit):
                h.main([
                    "--environment-id", "env-001",
                    "--authorization-ref", "approval-001",
                    "--output", "/tmp/should-not-exist-h01.json",
                    "--approved-readonly-observation",
                ])


if __name__ == "__main__":
    unittest.main()
