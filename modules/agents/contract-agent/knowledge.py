"""Model-free FoundryIQ contract; MCP retains its server-discovered schema."""

from __future__ import annotations

from typing import Literal, TypedDict
from urllib.parse import parse_qs, urlsplit

KB_API_VERSION = "2026-04-01"


class SemanticIntent(TypedDict):
    type: Literal["semantic"]
    search: str


class MinimalRetrieveRequest(TypedDict):
    intents: list[SemanticIntent]


def minimal_retrieve_request(question: str) -> MinimalRetrieveRequest:
    """Native REST acceptance payload, not a replacement for the MCP lane."""
    question = question.strip()
    if not question:
        raise ValueError("A self-contained retrieval question is required")
    return {"intents": [{"type": "semantic", "search": question}]}


def validate_minimal_mcp_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    segments = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".search.windows.net")
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in (None, 443)
        or len(segments) != 3
        or segments[0] != "knowledgebases"
        or not segments[1]
        or segments[2] != "mcp"
        or parse_qs(parsed.query, keep_blank_values=True) != {"api-version": [KB_API_VERSION]}
    ):
        raise ValueError("FoundryIQ requires a native Search KB MCP endpoint using GA 2026-04-01")
    return endpoint
