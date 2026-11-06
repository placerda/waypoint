from __future__ import annotations

import asyncio
from io import StringIO
import json
import logging
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx2 as httpx
from azure.core.credentials import AccessToken
from castia import Model
from castia.inference.tools import Tool
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from gateway import GatewayModel, GatewayProjectClient
from toolbox import assurance_tools


class TelemetryLoggingTests(unittest.TestCase):
    def test_exporter_errors_are_console_only_without_suppressing_application_logs(self) -> None:
        import main

        self.assertIsNotNone(main.app)
        captured = StringIO()
        handler = logging.StreamHandler(captured)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            for name in (
                "azure.monitor.opentelemetry.exporter",
                "microsoft.opentelemetry.a365.core.exporters",
            ):
                with self.subTest(logger=name):
                    exporter_logger = logging.getLogger(name)
                    self.assertFalse(exporter_logger.propagate)
                    self.assertTrue(exporter_logger.handlers)
                    with patch.object(exporter_logger.handlers[0], "emit") as console:
                        logging.getLogger(name + ".test").error("Export failed")
                    console.assert_called_once()
                    self.assertEqual(captured.getvalue(), "")
            logging.getLogger("toolbox").info("Application event")
            self.assertIn("Application event", captured.getvalue())
        finally:
            root.removeHandler(handler)
            handler.close()


class Credential:
    def __init__(self) -> None:
        self.scopes: list[str] = []

    async def get_token(self, *scopes: str, **kwargs: object) -> AccessToken:
        self.scopes.extend(scopes)
        return AccessToken("test-managed-token", int(time.time()) + 3600)

    async def close(self) -> None:
        pass


def response(output: list[dict], *, model: str = "selected-deployment") -> dict:
    return {
        "id": "resp-test",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": model,
        "output": output,
        "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
    }


def message(text: str) -> dict:
    return {
        "type": "message",
        "id": "msg-test",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler) -> GatewayProjectClient:
        self.credential = Credential()
        return GatewayProjectClient(
            "https://agent-account.services.ai.azure.com/api/projects/waypoint",
            "https://gateway.example.org/openai/v1/",
            "https://cognitiveservices.azure.com/.default",
            credential=self.credential,
            transport=httpx.MockTransport(handler),
        )

    async def test_inherited_tool_loop_mcp_usage_auth_and_trace(self) -> None:
        requests: list[httpx.Request] = []
        bodies: list[dict] = []
        executed: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            bodies.append(json.loads(request.content))
            if len(requests) == 1:
                output = [{
                    "type": "reasoning", "id": "reason-1", "summary": [],
                    "encrypted_content": "test-encrypted-reasoning",
                }, {
                    "type": "mcp_call", "id": "mcp-1", "server_label": "foundryiq",
                    "name": "knowledge_base_retrieve",
                    "arguments": '{"query":"BluePeak process error charges"}',
                    "output": '[{"ref_id":"0","title":"BluePeak section 4",'
                              '"content":"Supplier process error charges are not billable."}]',
                }, {
                    "type": "function_call", "id": "fc-1", "call_id": "call-1",
                    "name": "gather", "arguments": '{"invoice_id":"INV-2026-08034"}',
                    "status": "completed",
                }]
            else:
                output = [message("Escalate USD 111000; no payment executed.")]
            return httpx.Response(200, json=response(output))

        async def gather(activity, *, invoice_id: str) -> dict:
            executed.append(invoice_id)
            return {"invoice_id": invoice_id, "money_at_risk": 111000}

        client = self.client(handler)
        try:
            model = GatewayModel("selected-deployment", client, instructions="Use evidence.")
            mcp = {
                "type": "mcp", "server_label": "foundryiq",
                "server_url": "https://private-project.example.org/toolboxes/contracts/mcp",
                "require_approval": "never", "allowed_tools": ["knowledge_base_retrieve"],
                "authorization": "test-toolbox-token",
            }
            context = SpanContext(0x1234, 0x5678, False, TraceFlags(1))
            with trace.use_span(NonRecordingSpan(context)):
                text = await model.respond_with_tools(
                    "Investigate INV-2026-08034",
                    tools=[Tool(name="gather", description="Read invoice",
                                parameters={"type": "object", "properties": {
                                    "invoice_id": {"type": "string"}}}, impl=gather)],
                    activity=None, extra_specs=[mcp],
                )
            self.assertIn("111000", text)
            self.assertEqual(executed, ["INV-2026-08034"])
            self.assertIs(GatewayModel.respond_with_tools, Model.respond_with_tools)
            self.assertTrue(all(str(r.url) == "https://gateway.example.org/openai/v1/responses"
                                for r in requests))
            self.assertEqual(requests[0].headers["authorization"], "Bearer test-managed-token")
            self.assertIn("traceparent", requests[0].headers)
            self.assertEqual(bodies[0]["tools"][-1], mcp)
            self.assertEqual(bodies[1]["input"][-1]["type"], "function_call_output")
            self.assertEqual([x.get("type") for x in bodies[1]["input"]],
                             [None, "reasoning", "mcp_call",
                              "function_call", "function_call_output"])
            self.assertEqual(bodies[1]["input"][1]["encrypted_content"],
                             "test-encrypted-reasoning")
            self.assertIn('"ref_id":"0"', bodies[1]["input"][2]["output"])
            self.assertIn("reasoning.encrypted_content", bodies[0]["include"])
            raw = await client.get_openai_client().responses.create(
                model="selected-deployment", input="Usage check"
            )
            self.assertEqual(raw.usage.total_tokens, 14)
            self.assertEqual(set(self.credential.scopes),
                             {"https://cognitiveservices.azure.com/.default"})
        finally:
            await client.close()

    async def test_streaming_is_preserved(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertTrue(json.loads(request.content)["stream"])
            data = 'data: {"type":"response.output_text.delta","delta":"Evidence"}\n\n'
            return httpx.Response(200, text=data + "data: [DONE]\n\n",
                                  headers={"content-type": "text/event-stream"})

        client = self.client(handler)
        try:
            model = GatewayModel("selected-deployment", client, instructions="Ground claims.")
            self.assertEqual([x async for x in model.stream("Investigate")], ["Evidence"])
            self.assertIs(GatewayModel.stream, Model.stream)
        finally:
            await client.close()

    async def test_concurrent_investigations_do_not_share_continuation(self) -> None:
        counts: dict[str, int] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            invoice = body["input"][0]["content"]
            counts[invoice] = counts.get(invoice, 0) + 1
            if counts[invoice] == 1:
                output = [{
                    "type": "reasoning", "id": "reason-" + invoice, "summary": [],
                    "encrypted_content": "encrypted-" + invoice,
                }, {
                    "type": "function_call", "id": "fc-" + invoice,
                    "call_id": "call-" + invoice, "name": "lookup",
                    "arguments": "{}", "status": "completed",
                }]
            else:
                self.assertEqual(body["input"][1]["encrypted_content"],
                                 "encrypted-" + invoice)
                self.assertEqual(body["input"][2]["call_id"], "call-" + invoice)
                output = [message(invoice)]
            return httpx.Response(200, json=response(output))

        async def lookup(activity) -> dict:
            await asyncio.sleep(0)
            return {"status": "read"}

        client = self.client(handler)
        try:
            model = GatewayModel("selected-deployment", client, instructions="Use evidence.")
            tool = Tool(name="lookup", description="Read only",
                        parameters={"type": "object", "properties": {}}, impl=lookup)
            self.assertEqual(await asyncio.gather(*[
                model.respond_with_tools(invoice, tools=[tool], activity=None)
                for invoice in ("invoice-A", "invoice-B")
            ]), ["invoice-A", "invoice-B"])
        finally:
            await client.close()

    async def test_gateway_failure_never_falls_back_to_foundry(self) -> None:
        urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            return httpx.Response(403, json={"error": {"message": "Gateway denied"}})

        client = self.client(handler)
        try:
            with self.assertRaises(Exception):
                await GatewayModel("selected-deployment", client, instructions="").respond("test")
            self.assertEqual(urls, ["https://gateway.example.org/openai/v1/responses"])
        finally:
            await client.close()

    async def test_foundryiq_fails_closed_without_service_side_verification(self) -> None:
        with patch.dict(os.environ, {"TOOLBOX_NAME": "contract-toolbox",
                        "TOOLBOX_CONTRACT_TOOLBOX_MCP_ENDPOINT":
                            "https://private.example.org/toolboxes/contract-toolbox/mcp",
                        "WAYPOINT_FOUNDRYIQ_GATEWAY_VERIFIED": "false"}):
            with self.assertRaisesRegex(RuntimeError, "minimal MCP"):
                await assurance_tools()

    def test_project_and_gateway_are_distinct_endpoint_types(self) -> None:
        with self.assertRaises(ValueError):
            GatewayProjectClient("https://gateway.example.org/openai/v1/",
                                 "https://gateway.example.org/openai/v1/",
                                 "https://cognitiveservices.azure.com/.default")
        with self.assertRaises(ValueError):
            GatewayProjectClient("https://agent.example.org/api/projects/waypoint",
                                 "https://model.openai.azure.com/openai/v1/",
                                 "https://cognitiveservices.azure.com/.default")


if __name__ == "__main__":
    unittest.main()
