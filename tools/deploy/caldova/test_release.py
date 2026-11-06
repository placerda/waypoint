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

    def test_candidate_session_waits_only_for_provisioning(self):
        error = release.HttpResponseError("Agent is provisioning.")
        error.error = SimpleNamespace(code="agent_version_not_ready")
        session = SimpleNamespace(
            status="active", agent_session_id="candidate-session",
            version_indicator=SimpleNamespace(agent_version="5"),
        )
        client = Mock()
        client.agents.create_session.side_effect = [error, session]
        response = Mock()
        response.headers = {
            "x-agent-session-id": "candidate-session", "content-type": "application/json",
        }
        response.json.return_value = {
            "id": "actual-response", "status": "completed", "output_text": "Grounded answer.",
        }
        credential = Mock()
        credential.get_token.return_value.token = "synthetic-token"
        with patch.object(release.time, "sleep") as sleep, patch.object(
            release.requests, "post", return_value=response,
        ) as post:
            result = release.invoke(client, credential, "5", "Read-only question.")
        sleep.assert_called_once_with(10)
        self.assertEqual(client.agents.create_session.call_count, 2)
        for call in client.agents.create_session.call_args_list:
            self.assertEqual(call.kwargs["version_indicator"].agent_version, "5")
        self.assertEqual(post.call_args.kwargs["json"]["agent_session_id"], "candidate-session")
        self.assertEqual(result["response_id"], "actual-response")

    def test_non_provisioning_session_errors_are_not_retried(self):
        error = release.HttpResponseError("Access denied.")
        error.error = SimpleNamespace(code="Forbidden")
        client = Mock()
        client.agents.create_session.side_effect = error
        with patch.object(release.time, "sleep") as sleep:
            with self.assertRaises(release.HttpResponseError):
                release.invoke(client, Mock(), "5", "Read-only question.")
        sleep.assert_not_called()
        client.agents.create_session.assert_called_once()

    def test_candidate_provisioning_wait_is_bounded(self):
        error = release.HttpResponseError("Agent is provisioning.")
        error.error = SimpleNamespace(code="agent_version_not_ready")
        client = Mock()
        client.agents.create_session.side_effect = error
        with patch.object(release.time, "monotonic", side_effect=[100, 701]), patch.object(
            release.time, "sleep",
        ) as sleep:
            with self.assertRaises(TimeoutError):
                release.invoke(client, Mock(), "5", "Read-only question.")
        sleep.assert_not_called()

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
