"""Offline source-inventory regression tests: no app imports, no network, no broker."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from scripts.preproduction_inventory import inventory, main, markdown


class SourceInventoryTests(unittest.TestCase):
    def test_reports_route_and_indirect_method_alias_without_executing_anything(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = root / "backend/app/api/v1/endpoints/orders.py"
            service.parent.mkdir(parents=True)
            service.write_text(
                "@router.post('/send')\n"
                "def send(client):\n"
                "    method = client.place_order\n"
                "    method(quantity=1)\n"
            )
            result = inventory(root)
            self.assertFalse(result["live_broker_authorized"])
            self.assertEqual(result["production_decision"], "NO_GO")
            self.assertEqual(result["unapproved_order_method_references"][0]["kind"], "ALIAS")
            self.assertEqual(result["routes"][0]["path_fragment"], "/send")
            self.assertIn("NO-GO", markdown(result))

    def test_strict_gate_rejects_bypass_and_outputs_new_nonauthoritative_evidence(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "backend/app/services/legacy.py"
            path.parent.mkdir(parents=True)
            path.write_text("async def legacy(client):\n    await client.place_order(quantity=1)\n")
            destination = root / "evidence"
            self.assertEqual(main(["--root", str(root), "--output", str(destination), "--strict"]), 1)
            self.assertEqual(json.loads((destination / "inventory.json").read_text())["live_broker_authorized"], False)
            self.assertEqual(main(["--root", str(root), "--output", str(destination)]), 2)

    def test_canonical_and_transport_are_not_reported_as_unapproved_bypasses(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("backend/app/services/execution_service.py",
                         "backend/app/services/exchanges/kite/client.py",
                         "backend/app/services/simulation/sim_client.py"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("async def place(client):\n    await client.place_order(quantity=1)\n")
            result = inventory(root)
            self.assertEqual(result["unapproved_order_method_references"], [])
            self.assertEqual(len(result["broker_method_references"]), 3)

    def test_no_tree_is_not_a_false_success(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                inventory(Path(tmp))


if __name__ == "__main__":
    unittest.main()
