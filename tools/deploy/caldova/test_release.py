"""Offline contracts for the private release's fail-closed gates."""

import argparse
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import release


class ReleaseGates(unittest.TestCase):
    def candidate(self):
        return {
            "status": "candidate",
            "image": "crcaldovacus01.azurecr.io/agent@sha256:" + "a" * 64,
            "sourceSha": "b" * 40,
            "candidateVersion": "5",
            "previousVersion": "4",
            "agentPrincipalId": "expected-principal",
        }

    def test_dataset_has_three_distinct_read_only_cases(self):
        rows = [
            json.loads(line) for line in Path(release.__file__).with_name("cases.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({row["id"] for row in rows}), 3)
        for row in rows:
            self.assertIn("read-only", row["query"].lower())
            self.assertTrue(row["expected_behavior"])

    def test_active_version_rejects_latest_and_missing_pins(self):
        for value in (None, "", "latest", "0"):
            env = [{"name": "APP_FOUNDRY_RESPONSES_API_VERSION", "value": "v1"}]
            if value is not None:
                env.append({"name": "APP_FOUNDRY_ORCHESTRATOR_AGENT_VERSION", "value": value})
            app = {"properties": {"template": {"containers": [{"env": env}]}}}
            with self.subTest(value=value), patch.object(release, "az", return_value=app):
                with self.assertRaises(RuntimeError):
                    release.active_version()

    def test_active_numeric_pin_requires_verified_protocol(self):
        for protocol in ("v1", "2025-11-15-preview"):
            env = [
                {"name": "APP_FOUNDRY_ORCHESTRATOR_AGENT_VERSION", "value": "4"},
                {"name": "APP_FOUNDRY_RESPONSES_API_VERSION", "value": protocol},
            ]
            app = {"properties": {"template": {"containers": [{"env": env}]}}}
            with self.subTest(protocol=protocol), patch.object(release, "az", return_value=app):
                if protocol == "v1":
                    self.assertEqual(release.active_version(), "4")
                else:
                    with self.assertRaises(RuntimeError):
                        release.active_version()

    def test_candidate_digest_or_identity_mismatch_fails(self):
        selected = self.candidate()
        for image, principal in (
            ("different-image", "expected-principal"),
            (selected["image"], "different-principal"),
        ):
            client = Mock()
            client.agents.get_version.return_value = SimpleNamespace(
                definition=SimpleNamespace(container_configuration=SimpleNamespace(image=image)),
                instance_identity=SimpleNamespace(principal_id=principal),
            )
            with self.subTest(image=image, principal=principal):
                with self.assertRaises(RuntimeError):
                    release.verify_candidate(client, selected)

    def test_failed_or_missing_quality_cannot_promote(self):
        for status in ("failed", "candidate", None):
            selected = self.candidate()
            quality = {**selected, "status": status}
            args = argparse.Namespace(candidate=Path("candidate.json"), evaluation=Path("quality.json"))
            with patch.object(release, "read", side_effect=[selected, quality]), patch.object(release, "az") as azure:
                with self.assertRaises(RuntimeError):
                    release.promote(args, {}, Mock())
                azure.assert_not_called()

    def test_approval_cannot_be_reused_for_another_image_or_version(self):
        for key in ("image", "sourceSha", "candidateVersion", "previousVersion"):
            selected = self.candidate()
            quality = {**copy.deepcopy(selected), "status": "passed", key: "different"}
            args = argparse.Namespace(candidate=Path("candidate.json"), evaluation=Path("quality.json"))
            with self.subTest(key=key), patch.object(release, "read", side_effect=[selected, quality]), patch.object(release, "az") as azure:
                with self.assertRaises(RuntimeError):
                    release.promote(args, {}, Mock())
                azure.assert_not_called()

    def test_concurrent_active_version_change_prevents_promotion(self):
        selected = self.candidate()
        quality = {**selected, "status": "passed"}
        args = argparse.Namespace(candidate=Path("candidate.json"), evaluation=Path("quality.json"))
        with patch.object(release, "read", side_effect=[selected, quality]), patch.object(release, "verify_candidate"), patch.object(release, "active_version", return_value="6"), patch.object(release, "az") as azure:
            with self.assertRaisesRegex(RuntimeError, "baseline changed"):
                release.promote(args, {}, Mock())
            azure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
