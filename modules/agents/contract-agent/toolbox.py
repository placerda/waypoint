"""Execute native Foundry toolbox MCP inside the agent's private network."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import json
import logging
import os
from typing import Any, Iterator
from urllib.parse import parse_qs, urlsplit

import httpx
from castia import toolbox_token
from castia.inference.tools import Tool
from opentelemetry.propagate import inject

from gateway import required_env
from knowledge import validate_minimal_mcp_endpoint

LOGGER = logging.getLogger(__name__)
_NATIVE_EVIDENCE: ContextVar[dict[tuple[str, str], dict[str, Any]] | None] = ContextVar(
    "waypoint_native_foundryiq_evidence", default=None
)


@contextmanager
def foundryiq_evidence_scope() -> Iterator[None]:
    token = _NATIVE_EVIDENCE.set({})
    try:
        yield
    finally:
        _NATIVE_EVIDENCE.reset(token)


def recorded_foundryiq_evidence() -> list[dict[str, Any]]:
    evidence = _NATIVE_EVIDENCE.get()
    if not evidence:
        return []
    return [{
        "agent": "Native knowledge retrieval",
        "plane": "foundryiq",
        "provenance": "native-foundry-mcp",
        "summary": "Passages retrieved in this investigation, not an independent expert verdict.",
        "evidence": copy.deepcopy(list(evidence.values())),
    }]


def capture_foundryiq_evidence(
    passages: list[dict[str, Any]], references: list[dict[str, Any]]
) -> None:
    references_by_id = {item["ref_id"]: item for item in references}
    if len(references_by_id) != len(references):
        raise RuntimeError("Native knowledge MCP returned ambiguous reference IDs")
    captured: dict[tuple[str, str], dict[str, Any]] = {}
    for passage in passages:
        reference = references_by_id.get(passage.get("ref_id"))
        if reference is None:
            raise RuntimeError("Native knowledge passage has no matching source reference")
        source = reference.get("sourceData")
        if not isinstance(source, dict):
            raise RuntimeError("Native knowledge reference has no sourceData")
        title = source.get("title")
        uri = source.get("metadata_storage_path") or source.get("url")
        content = passage.get("content")
        if (
            not isinstance(title, str) or not title.strip()
            or not isinstance(uri, str) or not isinstance(content, str) or not content.strip()
        ):
            raise RuntimeError("Native knowledge source title, URI and passage are required")
        parsed = urlsplit(uri)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
        ):
            raise RuntimeError("Native knowledge source URI must be HTTPS without credentials")
        captured[(uri, content)] = {
            "claim": content,
            "source_ref": title,
            "source_uri": uri,
            "ref_id": reference["ref_id"],
            "provenance": "native-foundry-mcp",
        }
    evidence = _NATIVE_EVIDENCE.get()
    if evidence is not None:
        evidence.update(captured)


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
        LOGGER.info("Native toolbox MCP %s: acquiring agent token", method)
        try:
            token = await asyncio.wait_for(toolbox_token(), timeout=30)
        except TimeoutError:
            LOGGER.exception("Native toolbox MCP %s: agent token acquisition timed out", method)
            raise
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
            "Foundry-Features": "Toolboxes=V1Preview",
        }
        inject(headers)
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if method != "notifications/initialized":
            body["id"] = request_id
        LOGGER.info("Native toolbox MCP %s: sending authenticated request", method)
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            response = await client.post(self.endpoint, headers=headers, json=body)
        LOGGER.info("Native toolbox MCP %s: HTTP %s", method, response.status_code)
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
            capture_foundryiq_evidence(passages, references)
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
