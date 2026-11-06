"""Execute native Foundry toolbox MCP inside the agent's private network."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from castia import toolbox_token
from castia.inference.tools import Tool
from opentelemetry.propagate import inject

from gateway import required_env
from knowledge import validate_minimal_mcp_endpoint


class NativeToolbox:
    """BYO toolbox transport following the official raw MCP hosted-agent sample."""

    def __init__(self) -> None:
        self.endpoint = required_env("TOOLBOX_CONTRACT_TOOLBOX_MCP_ENDPOINT")
        endpoint = urlsplit(self.endpoint)
        project = urlsplit(required_env("FOUNDRY_PROJECT_ENDPOINT"))
        prefix = project.path.rstrip("/") + "/toolboxes/"
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or not endpoint.hostname.endswith(".services.ai.azure.com")
            or endpoint.netloc != project.netloc
            or endpoint.username or endpoint.password or endpoint.fragment
            or not endpoint.path.startswith(prefix)
            or not endpoint.path.endswith("/mcp")
            or parse_qs(endpoint.query) != {"api-version": ["v1"]}
        ):
            raise ValueError("Native MCP must target a toolbox in the configured Foundry project")
        self.session_id: str | None = None
        self.request_id = 0

    async def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.request_id += 1
        request_id = self.request_id
        headers = {
            "Authorization": "Bearer " + await toolbox_token(),
            "Accept": "application/json, text/event-stream",
            "Foundry-Features": "Toolboxes=V1Preview",
        }
        inject(headers)
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if method != "notifications/initialized":
            body["id"] = request_id
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            response = await client.post(self.endpoint, headers=headers, json=body)
        response.raise_for_status()
        if response.is_redirect:
            raise RuntimeError("Toolbox redirects are not accepted")
        self.session_id = response.headers.get("mcp-session-id", self.session_id)
        if method == "notifications/initialized":
            return {}
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            messages = [
                json.loads(line[5:].strip())
                for line in response.text.splitlines() if line.startswith("data:")
            ]
            matches = [
                item for item in messages
                if isinstance(item, dict) and item.get("id") == request_id
            ]
            if len(matches) != 1:
                raise RuntimeError("Toolbox SSE has no unique matching JSON-RPC response")
            payload = matches[0]
        else:
            payload = response.json()
        if not isinstance(payload, dict) or payload.get("id") != request_id:
            raise RuntimeError("Toolbox returned an invalid JSON-RPC response")
        if payload.get("error"):
            raise RuntimeError("Toolbox JSON-RPC error: " + json.dumps(payload["error"]))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Toolbox returned no MCP result")
        if result.get("isError"):
            raise RuntimeError("Native MCP tool reported an error: " + json.dumps(result))
        return result

    async def discover(self) -> Tool:
        await self.rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "caldova-waypoint", "version": "1.0.0"},
        })
        await self.rpc("notifications/initialized", {})
        result = await self.rpc("tools/list", {})
        name = required_env("WAYPOINT_KB_TOOL_NAME")
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise RuntimeError("Toolbox discovery returned no tool list")
        selected = [
            item for item in tools if isinstance(item, dict) and item.get("name") == name
        ]
        if len(selected) != 1 or not isinstance(selected[0].get("inputSchema"), dict):
            raise RuntimeError("Expected one native knowledge tool with a discovered input schema")
        tool = selected[0]

        async def invoke(activity: Any, **arguments: Any) -> dict[str, Any]:
            result = await self.rpc("tools/call", {"name": name, "arguments": arguments})
            evidence = [
                json.loads(item["text"])
                for item in result.get("content", [])
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            passages = [
                item for value in evidence if isinstance(value, list) for item in value
                if isinstance(item, dict) and item.get("content")
            ]
            references = [
                value for value in evidence if isinstance(value, dict)
                and value.get("kind") == "reference" and value.get("ref_id") is not None
            ]
            if not passages or not references:
                raise RuntimeError("Native knowledge MCP returned no grounding and source references")
            return result

        return Tool(
            name=name,
            description=tool.get("description", "") + (
                " Preserve grounding passages, reference IDs and source titles. "
                "This tool retrieves evidence without KB-side model synthesis."
            ),
            parameters=tool["inputSchema"],
            impl=invoke,
        )


async def assurance_tools() -> list[Tool]:
    if os.environ.get("WAYPOINT_FOUNDRYIQ_GATEWAY_VERIFIED", "").lower() != "true":
        raise RuntimeError("FoundryIQ minimal MCP/private routing has not been verified")
    if required_env("WAYPOINT_FOUNDRYIQ_RETRIEVAL_MODE") != "minimal-extractive":
        raise ValueError("Only model-free minimal-extractive FoundryIQ is supported")
    validate_minimal_mcp_endpoint(required_env("WAYPOINT_KB_MCP_ENDPOINT"))
    return [await NativeToolbox().discover()]
