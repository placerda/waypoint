import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ApplicationContractTests(unittest.TestCase):
    def test_native_byo_components_are_contained_pinned_and_least_privilege(self):
        definition = json.loads((ROOT / "app-definition.json").read_text())
        self.assertEqual(definition["id"], "caldova-waypoint")
        self.assertEqual({c["name"] for c in definition["components"]},
                         {"web", "api", "contract-agent"})
        for component in definition["components"]:
            self.assertTrue((ROOT / component["path"]).resolve().is_relative_to(ROOT))
            self.assertTrue((ROOT / component["path"] / "Dockerfile").is_file())
            self.assertRegex(component["source"]["commit"], r"^[0-9a-f]{40}$")
            self.assertNotEqual(component["source"]["commit"], "0" * 40)
            self.assertNotIn("model-user", component["profiles"])
            self.assertNotIn("conversation-store", component["profiles"])
            self.assertNotIn("roles", component)
            if component["kind"] == "containerapp":
                self.assertEqual(component["ingress"], "external")
                if component["name"] == "api":
                    settings = {s["key"]: s for s in component["settings"]}
                    self.assertIs(settings["APP_DATABASE_ENTRA_ENABLED"]["value"], True)
                    self.assertNotIn("value", settings["APP_DATABASE_ENTRA_CLIENT_ID"])
                    self.assertTrue({
                        "APP_DATABASE_BOOTSTRAP_CONNECTION",
                        "APP_DATABASE_ENTRA_PRINCIPAL_ID",
                        "APP_DATABASE_BOOTSTRAP_CLIENT_ID",
                    }.isdisjoint(settings))
            else:
                self.assertNotIn("resources", component)
                self.assertNotIn("ingress", component)

    def test_bundled_seed_is_native_and_single_invoice(self):
        original = json.loads((ROOT / "modules/corpus/data/waypoint/waypoint-seed.json").read_text())
        bounded = json.loads((ROOT / "apps/waypoint/api/corpus/data/waypoint/waypoint-seed.json")
                             .read_text())
        self.assertEqual(len(bounded["invoices"]), 1)
        invoice = bounded["invoices"][0]
        self.assertEqual(invoice, next(i for i in original["invoices"]
                                      if i["id"] == "INV-2026-08034"))
        for document in bounded["contract_documents"]:
            self.assertEqual((ROOT / "modules/corpus" / document["uri"]).read_bytes(),
                             (ROOT / "apps/waypoint/api/corpus" / document["uri"]).read_bytes())


if __name__ == "__main__":
    unittest.main()
