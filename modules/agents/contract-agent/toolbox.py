"""Real remote MCP grounding; all query planning and synthesis stay in the agent."""

from __future__ import annotations

from collections.abc import Sequence
import os
from typing import Any

from castia import (
    apply_optimized_toolbox_tools,
    resolve_toolbox_endpoint,
    toolbox_mcp_tool,
    toolbox_token,
)
from gateway import required_env
from knowledge import validate_minimal_mcp_endpoint

_OPTIMIZER_ENDPOINT = "https://example.invalid/toolboxes/contract-toolbox/mcp?api-version=v1"
_KB_TOOL_NAME = "contracts-kb-mcp___knowledge_base_retrieve"
_KB_TOOL_DESCRIPTION = (
    "Retrieve grounding passages from Caldova's governing contracts and "
    "billing/quality policies - statements of work, rate cards, and "
    "release/billability policies. Use for any question about what a supplier's "
    "contract or a Caldova policy permits, requires, or prohibits: whether a "
    "fee is billable, rate or threshold lookups, and clause interpretation. "
    "Returns cited source passages; ground every contractual claim in them and "
    "never assert a term this tool does not return. This is minimal/extractive "
    "retrieval, not a KB-generated answer. Follow the actual MCP-discovered "
    "input schema; do not assume REST messages or invent parameter names. "
    "Use focused searches naming the supplier and disputed fee, repeat searches "
    "when needed, and retain grounding ref_id, title and document identifiers."
)


def is_toolbox_configured() -> bool:
    return resolve_toolbox_endpoint() is not None


def _foundryiq_spec(
    endpoint: str, *, token: str | None = None, tool_name: str = _KB_TOOL_NAME
) -> dict[str, Any]:
    spec = toolbox_mcp_tool(
        endpoint,
        server_label="foundryiq",
        allowed_tools=(tool_name,),
        token=token,
        descriptions={tool_name: _KB_TOOL_DESCRIPTION},
    )
    assert spec is not None
    return spec


def foundryiq_toolbox_tools() -> list[dict[str, Any]]:
    """Optimizer-visible baseline for the FoundryIQ MCP tool."""
    return [_foundryiq_spec(_OPTIMIZER_ENDPOINT)]


async def _runtime_foundryiq_spec(
    tool_definitions: Sequence[dict[str, Any]] = (),
) -> dict[str, Any] | None:
    if os.environ.get("WAYPOINT_FOUNDRYIQ_GATEWAY_VERIFIED", "").lower() != "true":
        raise RuntimeError(
            "FoundryIQ minimal MCP/private routing has not been verified"
        )
    if required_env("WAYPOINT_FOUNDRYIQ_RETRIEVAL_MODE") != "minimal-extractive":
        raise ValueError("Only model-free minimal-extractive FoundryIQ is supported")
    validate_minimal_mcp_endpoint(required_env("WAYPOINT_KB_MCP_ENDPOINT"))
    endpoint = required_env("TOOLBOX_CONTRACT_TOOLBOX_MCP_ENDPOINT")
    spec = _foundryiq_spec(
        endpoint, token=await toolbox_token(), tool_name=required_env("WAYPOINT_KB_TOOL_NAME")
    )
    return apply_optimized_toolbox_tools(spec, list(tool_definitions))


async def read_specs(tool_definitions: Sequence[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    spec = await _runtime_foundryiq_spec(tool_definitions)
    return [spec] if spec else []


async def assurance_specs(
    tool_definitions: Sequence[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    return await read_specs(tool_definitions)
