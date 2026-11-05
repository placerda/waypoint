"""Pinned Castia 0.7.7 transport seam: Responses go only through the AI gateway."""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

import httpx2 as httpx
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from castia import Model
from openai import AsyncOpenAI
from openai.resources.responses import AsyncResponses
from opentelemetry.propagate import inject

if TYPE_CHECKING:
    from azure.ai.projects.aio._patch import AsyncOpenAI as ProjectOpenAI


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be explicitly configured")
    return value


def https_endpoint(value: str, *, suffix: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.rstrip("/").endswith(suffix.rstrip("/"))
    ):
        raise ValueError(f"Expected an HTTPS endpoint ending in {suffix}")
    return value.rstrip("/") + "/"


async def propagate_trace(request: httpx.Request) -> None:
    headers: dict[str, str] = {}
    inject(headers)
    request.headers.update(headers)


@dataclass
class _Continuation:
    original: list[Any]
    consumed: int
    expanded: list[Any]
    output: list[dict[str, Any]]


class GatewayResponses(AsyncResponses):
    """Restore items omitted by Castia's function-only conversation echo.

    State is task-local and limited to the last response, never shared across
    concurrent investigations. Castia still executes and bounds its own loop.
    """

    def __init__(self, client: AsyncOpenAI) -> None:
        super().__init__(client)
        self._continuation: ContextVar[_Continuation | None] = ContextVar(
            "waypoint_response_continuation", default=None
        )

    async def create(self, **kwargs: Any) -> Any:
        original = kwargs.get("input")
        if not isinstance(original, list) or kwargs.get("stream"):
            return await super().create(**kwargs)

        previous = self._continuation.get()
        expanded = list(original)
        if previous is not None and original is previous.original:
            echoed = {
                item["call_id"] for item in previous.output
                if item.get("type") == "function_call"
            }
            additions = [
                item for item in original[previous.consumed:]
                if not (
                    isinstance(item, dict) and item.get("type") == "function_call"
                    and item.get("call_id") in echoed
                )
            ]
            expanded = [*previous.expanded, *previous.output, *additions]
        kwargs["input"] = expanded
        if kwargs.get("reasoning"):
            kwargs["include"] = list(dict.fromkeys([
                *kwargs.get("include", []), "reasoning.encrypted_content",
            ]))
        consumed = len(original)
        result = await super().create(**kwargs)
        self._continuation.set(_Continuation(
            original, consumed, expanded,
            [item.model_dump(mode="json", exclude_none=True) for item in result.output],
        ))
        return result


class GatewayOpenAI(AsyncOpenAI):
    @cached_property
    def responses(self) -> GatewayResponses:
        return GatewayResponses(self)


class GatewayProjectClient(AIProjectClient):
    """Keep the real project endpoint, but replace its model transport only.

    Castia's inherited tool loop, streaming and plain response methods all call
    get_openai_client(). Nothing routes via the agent account's model endpoint.
    """

    def __init__(
        self,
        project_endpoint: str,
        gateway_base_url: str,
        scope: str,
        *,
        credential: Any | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        project_endpoint = https_endpoint(
            project_endpoint, suffix=urlsplit(project_endpoint).path
        ).rstrip("/")
        if "/api/projects/" not in urlsplit(project_endpoint).path:
            raise ValueError("FOUNDRY_PROJECT_ENDPOINT must be a Foundry project endpoint")
        gateway_base_url = https_endpoint(gateway_base_url, suffix="/openai/v1/")
        host = urlsplit(gateway_base_url).hostname or ""
        if host.endswith((".services.ai.azure.com", ".openai.azure.com")):
            raise ValueError("A direct Foundry/model endpoint is not an AI gateway")
        if scope != "https://cognitiveservices.azure.com/.default":
            raise ValueError("Gateway audience must be the Cognitive Services managed-auth scope")
        self._gateway_credential = credential or DefaultAzureCredential()
        super().__init__(endpoint=project_endpoint, credential=self._gateway_credential)
        self._gateway_openai = GatewayOpenAI(
            base_url=gateway_base_url,
            api_key=get_bearer_token_provider(self._gateway_credential, scope),
            max_retries=2,
            http_client=httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(120.0, connect=15.0),
                event_hooks={"request": [propagate_trace]},
                follow_redirects=False,
            ),
        )

    def get_openai_client(
        self, agent_name: str | None = None, **kwargs: Any
    ) -> ProjectOpenAI:
        if agent_name is not None or kwargs:
            raise ValueError("Project model overrides are not supported by the gateway adapter")
        # The SDK stub specializes evals, but uses native AsyncOpenAI at runtime.
        return cast("ProjectOpenAI", self._gateway_openai)

    async def close(self) -> None:
        await self._gateway_openai.close()
        await super().close()
        await self._gateway_credential.close()


class GatewayModel(Model):
    """Initialize the pinned model's fields without constructing a direct model client.

    The private-field seam is deliberately isolated and regression-tested against
    Castia 0.7.7. All inference/tool-loop behavior remains inherited, not copied.
    """

    def __init__(
        self,
        deployment: str,
        client: GatewayProjectClient,
        *,
        instructions: str,
        reasoning_effort: str = "low",
    ) -> None:
        if not deployment.strip():
            raise ValueError("A gateway model deployment is required")
        if reasoning_effort not in {"minimal", "low", "medium", "high"}:
            raise ValueError("Unsupported MODEL_REASONING_EFFORT for Castia 0.7.7")
        self._deployment = deployment
        self._instructions = instructions
        self._tool_definitions = ()
        self._reasoning_effort = reasoning_effort
        self._reasoning = {"reasoning": {"effort": reasoning_effort}}
        self._client = client


def configured_gateway_model(instructions: str) -> GatewayModel:
    return GatewayModel(
        required_env("AZURE_AI_MODEL_DEPLOYMENT_NAME"),
        GatewayProjectClient(
            required_env("FOUNDRY_PROJECT_ENDPOINT"),
            required_env("WAYPOINT_GATEWAY_BASE_URL"),
            required_env("WAYPOINT_GATEWAY_SCOPE"),
        ),
        instructions=instructions,
        reasoning_effort=os.environ.get("MODEL_REASONING_EFFORT", "low"),
    )
