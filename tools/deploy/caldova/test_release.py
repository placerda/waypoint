"""Offline contracts for the private release's fail-closed gates."""

import argparse
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tarfile
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

    def quality(self):
        return {
            **self.candidate(), "status": "passed", "nativeRunStatus": "completed",
            "resultCounts": {"total": 3, "passed": 3, "failed": 0, "errored": 0},
            "evaluationId": "synthetic-eval", "evaluationRunId": "synthetic-run",
            "reportUrl": "https://example.invalid/evaluation",
        }

    def web_build(self):
        return {
            "status": "built", "component": "web", "sourceSha": self.candidate()["sourceSha"],
            "image": release.REGISTRY + ".azurecr.io/agent-landing-zone/custom/web@sha256:" + "c" * 64,
            "acrRunId": "synthetic-web-build",
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
        quality = self.quality()
        args = argparse.Namespace(candidate=Path("candidate.json"), evaluation=Path("quality.json"))
        with patch.object(release, "read", side_effect=[selected, quality]), patch.object(release, "verify_candidate"), patch.object(release, "active_version", return_value="6"), patch.object(release, "az") as azure:
            with self.assertRaisesRegex(RuntimeError, "baseline changed"):
                release.promote(args, {}, Mock())
            azure.assert_not_called()

    def test_native_completion_and_all_three_integer_results_are_required(self):
        args = SimpleNamespace(candidate=Path("candidate.json"), evaluation=Path("quality.json"))
        invalid = [
            {"nativeRunStatus": value} for value in (None, "queued", "failed")
        ] + [
            {"resultCounts": value} for value in (
                None, {}, {"total": 3, "passed": 3, "failed": False, "errored": 0},
                {"total": 3, "passed": 2, "failed": 1, "errored": 0},
                {"total": 3, "passed": 3, "failed": 0, "errored": 1},
                {"total": 4, "passed": 3, "failed": 0, "errored": 0},
            )
        ] + [{key: ""} for key in ("evaluationId", "evaluationRunId")]
        for changed in invalid:
            quality = {**self.quality(), **changed}
            with self.subTest(changed=changed), patch.object(
                release, "read", side_effect=[self.candidate(), quality],
            ), patch.object(release, "az") as azure:
                with self.assertRaisesRegex(RuntimeError, "Completed native evaluation"):
                    release.promote(args, {}, Mock())
                azure.assert_not_called()

    def test_web_receipt_must_be_the_built_immutable_matching_component(self):
        args = SimpleNamespace(
            candidate=Path("candidate.json"), evaluation=Path("quality.json"), web_build=Path("web.json"),
        )
        invalid = [
            {"status": "failed"}, {"component": "api"}, {"sourceSha": "different"},
            {"image": "crcaldovacus01.azurecr.io/web:latest"}, {"acrRunId": ""},
            {"image": "other.azurecr.io/agent-landing-zone/custom/web@sha256:" + "c" * 64},
        ]
        for changed in invalid:
            with self.subTest(changed=changed), patch.object(
                release, "read",
                side_effect=[self.candidate(), self.quality(), {**self.web_build(), **changed}],
            ), patch.object(release, "verify_candidate"), patch.object(
                release, "active_version", return_value="4",
            ), patch.object(release, "az") as azure:
                with self.assertRaisesRegex(RuntimeError, "verified immutable web build"):
                    release.promote(args, {}, Mock())
                azure.assert_not_called()

    def test_web_build_uses_private_pool_exact_archive_and_source_version(self):
        source = "b" * 40
        bundle = io.BytesIO()
        with tarfile.open(fileobj=bundle, mode="w") as archive:
            contents = b"FROM scratch\n"
            member = tarfile.TarInfo("Dockerfile")
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
        proof = {}
        with patch.object(release, "command", side_effect=[source, ""]), patch.object(
            release.subprocess, "run", return_value=SimpleNamespace(stdout=bundle.getvalue()),
        ) as git, patch.object(release, "az", side_effect=[
            {"runId": "synthetic-build"},
            {"status": "Succeeded", "outputImages": [{
                "repository": "agent-landing-zone/custom/web",
                "tag": source[:12] + "-42", "digest": "sha256:" + "c" * 64,
            }]},
        ]) as azure, patch.dict(release.os.environ, {"GITHUB_SHA": source, "GITHUB_RUN_ID": "42"}):
            release.build(SimpleNamespace(component="web"), proof)
        self.assertIn(source + ":apps/waypoint/web", git.call_args.args[0])
        self.assertIn("privatebuild", azure.call_args_list[0].args)
        self.assertIn("BUILD_VERSION=" + source[:12], azure.call_args_list[0].args)
        self.assertEqual(proof["component"], "web")
        self.assertEqual(proof["image"], self.web_build()["image"])
        self.assertEqual(proof["status"], "built")

    def application(self, name, image, pin="4"):
        return {
            "properties": {
                "configuration": {
                    "activeRevisionsMode": "Single",
                    "ingress": {"fqdn": name + ".example.invalid"},
                },
                "template": {"containers": [{
                    "image": image,
                    "env": [
                        {"name": "APP_FOUNDRY_ORCHESTRATOR_AGENT_VERSION", "value": pin},
                        {"name": "APP_FOUNDRY_RESPONSES_API_VERSION", "value": "v1"},
                    ],
                }]},
                "latestRevisionName": "ready", "latestReadyRevisionName": "ready",
            },
        }

    def promotion(self, failure=None):
        state = {
            "pin": "4",
            "api": release.REGISTRY + ".azurecr.io/api@sha256:" + "d" * 64,
            "web": release.REGISTRY + ".azurecr.io/web@sha256:" + "e" * 64,
        }
        original_web = state["web"]
        updated_web = self.web_build()["image"]
        updates = []
        proof = {}
        failed_once = False

        def azure(*args):
            name = args[args.index("--name") + 1]
            component = "api" if name == release.API else "web"
            if args[:2] == ("containerapp", "show"):
                return self.application(name, state[component], state["pin"])
            self.assertEqual(args[:2], ("containerapp", "update"))
            if "--image" in args:
                image = args[args.index("--image") + 1]
                updates.append((component, image))
                if failure == "recovery" and image == original_web:
                    raise RuntimeError("Synthetic recovery failure.")
                state[component] = image
            else:
                pin = args[args.index("--set-env-vars") + 1].split("=", 1)[1]
                updates.append(("pin", pin))
                state["pin"] = pin
            return {}

        def wait(name, image, health_path):
            nonlocal failed_once
            if not failed_once:
                if failure in ("web", "recovery") and name == release.WEB and image == updated_web:
                    failed_once = True
                    raise TimeoutError("Synthetic web readiness failure.")
                if failure == "api" and name == release.API:
                    failed_once = True
                    raise TimeoutError("Synthetic API readiness failure.")
                if failure == "concurrent" and name == release.WEB:
                    state["pin"] = "9"
                if failure == "concurrent-candidate" and name == release.WEB:
                    state["pin"] = "5"
                if failure == "concurrent-image" and name == release.WEB:
                    state["api"] = release.REGISTRY + ".azurecr.io/api@sha256:" + "f" * 64
            return "https://" + name + ".example.invalid"

        args = SimpleNamespace(
            candidate=Path("candidate.json"), evaluation=Path("quality.json"), web_build=Path("web.json"),
        )
        with patch.object(
            release, "read", side_effect=[self.candidate(), self.quality(), self.web_build()],
        ), patch.object(release, "verify_candidate"), patch.object(
            release, "az", side_effect=azure,
        ), patch.object(release, "wait_for_application", side_effect=wait), patch.object(
            release.requests, "get", return_value=SimpleNamespace(status_code=401),
        ):
            if failure:
                with self.assertRaises((RuntimeError, TimeoutError)):
                    release.promote(args, proof, Mock())
            else:
                release.promote(args, proof, Mock())
        return state, updates, proof, original_web

    def test_matching_web_precedes_agent_promotion(self):
        state, updates, proof, original_web = self.promotion()
        self.assertEqual(updates, [("web", self.web_build()["image"]), ("pin", "5")])
        self.assertEqual(state["pin"], "5")
        self.assertEqual(proof["status"], "promoted")
        self.assertEqual(proof["previousWebImage"], original_web)
        self.assertEqual(proof["webImage"], self.web_build()["image"])

    def test_web_readiness_failure_restores_web_without_moving_agent(self):
        state, updates, proof, original_web = self.promotion("web")
        self.assertEqual(updates, [("web", self.web_build()["image"]), ("web", original_web)])
        self.assertEqual(state["pin"], "4")
        self.assertEqual(state["web"], original_web)
        self.assertEqual(proof["recoveryStatus"], "restored")
        self.assertNotEqual(proof.get("status"), "promoted")

    def test_api_readiness_failure_restores_pin_then_web(self):
        state, updates, proof, original_web = self.promotion("api")
        self.assertEqual(updates, [
            ("web", self.web_build()["image"]), ("pin", "5"), ("pin", "4"), ("web", original_web),
        ])
        self.assertEqual(state["pin"], "4")
        self.assertEqual(state["web"], original_web)
        self.assertEqual(proof["recoveryStatus"], "restored")
        self.assertNotEqual(proof.get("status"), "promoted")

    def test_recovery_failure_is_not_reported_as_restored_or_promoted(self):
        state, _, proof, _ = self.promotion("recovery")
        self.assertEqual(state["pin"], "4")
        self.assertEqual(state["web"], self.web_build()["image"])
        self.assertEqual(proof["recoveryStatus"], "failed")
        self.assertNotEqual(proof.get("status"), "promoted")

    def test_concurrent_pin_or_image_is_not_overwritten_during_recovery(self):
        for failure in ("concurrent", "concurrent-candidate", "concurrent-image"):
            with self.subTest(failure=failure):
                state, updates, proof, _ = self.promotion(failure)
                self.assertEqual(updates, [("web", self.web_build()["image"])])
                self.assertEqual(proof["recoveryStatus"], "failed")
                if failure == "concurrent":
                    self.assertEqual(state["pin"], "9")
                elif failure == "concurrent-candidate":
                    self.assertEqual(state["pin"], "5")
                else:
                    self.assertTrue(state["api"].endswith("f" * 64))

    def test_multiple_revision_or_unpinned_baseline_is_rejected(self):
        for image, mode in (("image:latest", "Single"), (self.web_build()["image"], "Multiple")):
            app = self.application(release.WEB, image)
            app["properties"]["configuration"]["activeRevisionsMode"] = mode
            with self.subTest(image=image, mode=mode), self.assertRaises(RuntimeError):
                release.application_image(app)

    def test_ready_image_is_required_before_health_success(self):
        app = self.application(release.WEB, self.web_build()["image"])
        with patch.object(release, "az", return_value=app), patch.object(
            release.requests, "get", return_value=SimpleNamespace(status_code=200),
        ) as get:
            release.wait_for_application(release.WEB, self.web_build()["image"], "/")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        app["properties"]["latestReadyRevisionName"] = "old"
        with patch.object(release, "az", return_value=app), patch.object(
            release.time, "monotonic", side_effect=[0, 0, 181],
        ), patch.object(release.time, "sleep"), patch.object(release.requests, "get") as get:
            with self.assertRaises(TimeoutError):
                release.wait_for_application(release.WEB, self.web_build()["image"], "/")
            get.assert_not_called()

    def test_anonymous_redirect_is_not_accepted_as_denial(self):
        for code in (200, 302, 401):
            with self.subTest(code=code), patch.object(
                release.requests, "get", return_value=SimpleNamespace(status_code=code),
            ) as get:
                if code == 401:
                    release.verify_anonymous_denial("https://example.invalid")
                else:
                    with self.assertRaises(RuntimeError):
                        release.verify_anonymous_denial("https://example.invalid")
                self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_health_connection_failures_are_bounded_and_can_recover(self):
        app = self.application(release.WEB, self.web_build()["image"])
        with patch.object(release, "az", return_value=app), patch.object(
            release.requests, "get", side_effect=[
                release.requests.ConnectionError("Synthetic startup connection failure."),
                SimpleNamespace(status_code=200),
            ],
        ), patch.object(release.time, "sleep") as sleep:
            release.wait_for_application(release.WEB, self.web_build()["image"], "/")
        sleep.assert_called_once_with(5)

    def test_wrong_live_image_is_rejected_before_health(self):
        app = self.application(
            release.WEB, release.REGISTRY + ".azurecr.io/web@sha256:" + "f" * 64,
        )
        with patch.object(release, "az", return_value=app), patch.object(
            release.requests, "get",
        ) as get:
            with self.assertRaisesRegex(RuntimeError, "image changed"):
                release.wait_for_application(release.WEB, self.web_build()["image"], "/")
            get.assert_not_called()

    def test_workflow_gates_matching_web_build_and_requires_its_receipt(self):
        workflow = (release.ROOT / ".github" / "workflows" / "caldova-release.yml").read_text()
        self.assertLess(workflow.index("release.py evaluate"), workflow.index("release.py build --component web"))
        self.assertLess(workflow.index("release.py build --component web"), workflow.index("release.py promote"))
        self.assertIn('--web-build "$PROOF/web-build.json"', workflow)
        self.assertIn("!inputs.source_only", workflow)
        self.assertIn("environment: caldova-private", workflow)


if __name__ == "__main__":
    unittest.main()
