from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge import minimal_retrieve_request, validate_minimal_mcp_endpoint
from toolbox import NativeToolbox, assurance_tools

NATIVE = (
    "https://private.search.windows.net/knowledgebases/contracts/mcp"
    "?api-version=2026-08-01-preview"
)
CONFIG = {
    "WAYPOINT_FOUNDRYIQ_GATEWAY_VERIFIED": "true",
    "WAYPOINT_FOUNDRYIQ_RETRIEVAL_MODE": "minimal-extractive",
    "WAYPOINT_KB_MCP_ENDPOINT": NATIVE,
    "FOUNDRY_PROJECT_ENDPOINT":
        "https://agent.services.ai.azure.com/api/projects/demo",
    "TOOLBOX_CONTRACT_TOOLBOX_MCP_ENDPOINT":
        "https://agent.services.ai.azure.com/api/projects/demo/toolboxes/contracts/mcp?api-version=v1",
    "WAYPOINT_KB_TOOL_NAME": "knowledge_base___knowledge_base_retrieve",
}
SCHEMA = {
    "type": "object", "properties": {"query_variants": {
        "type": "array", "items": {"type": "string"},
    }}, "required": ["query_variants"], "additionalProperties": False,
}


class MinimalFoundryIQTests(unittest.IsolatedAsyncioTestCase):
    def test_ga_rest_request_has_only_supported_intents(self):
        self.assertEqual(minimal_retrieve_request(" BluePeak process error charges "), {
            "intents": [{"type": "semantic", "search": "BluePeak process error charges"}],
        })
        with self.assertRaises(ValueError):
            minimal_retrieve_request(" ")
        self.assertEqual(validate_minimal_mcp_endpoint(NATIVE), NATIVE)
        for endpoint in (
            NATIVE.replace("2026-08-01-preview", "2026-04-01"),
            NATIVE.replace("/mcp?", "/retrieve?"),
            NATIVE.replace("https:", "http:"),
            NATIVE + "&outputMode=answerSynthesis",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                validate_minimal_mcp_endpoint(endpoint)

    async def exercise(self, *, failure=None, sse=False):
        requests = []
        token = AsyncMock(return_value="test-project-token")

        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            self.assertEqual(request.headers["Authorization"], "Bearer test-project-token")
            self.assertIn("api-version=v1", str(request.url))
            method = body["method"]
            if method == "notifications/initialized":
                self.assertEqual(request.headers["mcp-session-id"], "session-1")
                return httpx.Response(202)
            result = {
                "initialize": {"serverInfo": {"name": "Foundry"}},
                "tools/list": {"tools": [{
                    "name": CONFIG["WAYPOINT_KB_TOOL_NAME"], "description": "Retrieve contracts",
                    "inputSchema": SCHEMA,
                }]},
                "tools/call": {"content": [
                    {"type": "text", "text": json.dumps([{
                        "ref_id": 0, "content": "BluePeak charges USD 118 per excess hour",
                    }])},
                    {"type": "text", "text": json.dumps({
                        "kind": "reference", "ref_id": 0, "uri": "https://private/source",
                    })},
                ]},
            }[method]
            payload = {"jsonrpc": "2.0", "id": body["id"], "result": result}
            if failure and method == "tools/call":
                if failure == "rpc":
                    payload = {"jsonrpc": "2.0", "id": body["id"],
                               "error": {"code": -32000, "message": "Denied"}}
                elif failure == "tool":
                    payload["result"] = {"isError": True, "content": []}
                elif failure == "grounding":
                    payload["result"] = {"content": []}
                elif failure == "http":
                    return httpx.Response(403, json={"error": "Denied"})
            if sse:
                return httpx.Response(200, text="event: message\ndata: " + json.dumps(payload) + "\n\n",
                                      headers={"content-type": "text/event-stream",
                                               "mcp-session-id": "session-1"})
            return httpx.Response(200, json=payload, headers={"mcp-session-id": "session-1"})

        original = httpx.AsyncClient
        with patch.dict(os.environ, CONFIG), patch("toolbox.toolbox_token", token), patch(
            "toolbox.httpx.AsyncClient",
            side_effect=lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
        ):
            tool, = await assurance_tools()
            self.assertEqual(tool.parameters, SCHEMA)
            self.assertEqual(tool.spec()["type"], "function")
            self.assertNotIn("server_url", tool.spec())
            result = await tool.run(None, query_variants=["BluePeak excess rate"])
            self.assertEqual(requests[-1]["params"]["arguments"],
                             {"query_variants": ["BluePeak excess rate"]})
            self.assertEqual(token.await_count, 4)
            return result

    async def test_native_discovery_and_reference_zero_are_preserved(self):
        result = await self.exercise()
        self.assertEqual(json.loads(result["content"][1]["text"])["ref_id"], 0)

    async def test_native_sse_is_supported(self):
        self.assertIn("content", await self.exercise(sse=True))

    async def test_agent_token_timeout_is_bounded_and_prevents_http(self):
        with patch.dict(os.environ, CONFIG), patch(
            "toolbox.toolbox_token", AsyncMock(side_effect=TimeoutError)
        ), patch("toolbox.asyncio.wait_for", wraps=asyncio.wait_for) as deadline, patch(
            "toolbox.httpx.AsyncClient"
        ) as client, self.assertLogs("toolbox", level="ERROR") as logs:
            with self.assertRaises(TimeoutError):
                await NativeToolbox().rpc("initialize", {})
        self.assertEqual(deadline.call_args.kwargs["timeout"], 30)
        client.assert_not_called()
        self.assertIn("agent token acquisition timed out", logs.output[0])

    async def test_native_errors_fail_closed(self):
        for failure in ("rpc", "tool", "grounding", "http"):
            with self.subTest(failure=failure), self.assertRaises(
                (RuntimeError, httpx.HTTPStatusError)
            ):
                await self.exercise(failure=failure)

    async def test_gate_and_model_free_mode_are_enforced(self):
        for changed, expected in [
            ({"WAYPOINT_FOUNDRYIQ_GATEWAY_VERIFIED": "false"}, RuntimeError),
            ({"WAYPOINT_FOUNDRYIQ_RETRIEVAL_MODE": "synthesis"}, ValueError),
            ({"WAYPOINT_KB_MCP_ENDPOINT": NATIVE.replace(
                "2026-08-01-preview", "2026-04-01")}, ValueError),
        ]:
            with patch.dict(os.environ, {**CONFIG, **changed}), self.assertRaises(expected):
                await assurance_tools()

    def test_project_token_cannot_be_sent_to_another_endpoint(self):
        for endpoint in [
            CONFIG["TOOLBOX_CONTRACT_TOOLBOX_MCP_ENDPOINT"].replace("agent.services", "other.services"),
            CONFIG["TOOLBOX_CONTRACT_TOOLBOX_MCP_ENDPOINT"].replace("/demo/", "/other/"),
            CONFIG["TOOLBOX_CONTRACT_TOOLBOX_MCP_ENDPOINT"].replace("https:", "http:"),
        ]:
            with patch.dict(os.environ, {
                **CONFIG, "TOOLBOX_CONTRACT_TOOLBOX_MCP_ENDPOINT": endpoint,
            }), self.assertRaises(ValueError):
                NativeToolbox()
