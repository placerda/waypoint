"""Minimal Foundry hosted-agent Responses client."""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from opentelemetry.propagate import inject

AI_SCOPE = "https://ai.azure.com/.default"
FOUNDRY_FEATURES = "HostedAgents=V1Preview,AgentEndpoints=V1Preview"


@dataclass(frozen=True)
class HostedResponseStart:
    response_id: str
    status: str


class FoundryInvocationError(RuntimeError):
    """Raised when a hosted response cannot be started."""


class FoundryResponsesClient:
    def __init__(
        self,
        *,
        project_endpoint: str,
        api_version: str,
        timeout_seconds: float,
        agent_version: str | None = None,
    ) -> None:
        self.project_endpoint = project_endpoint.rstrip("/")
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds
        if agent_version is not None and not re.fullmatch(r"[1-9][0-9]*", agent_version):
            raise ValueError("A promoted agent version must be a positive numeric version.")
        self.agent_version = agent_version

    def _create_pinned_session(self, *, agent_name: str, headers: dict[str, str]) -> str:
        deadline = time.monotonic() + self.timeout_seconds
        endpoint = (
            f"{self.project_endpoint}/agents/{quote(agent_name, safe='')}/endpoint/sessions"
            "?api-version=v1"
        )
        request = urllib.request.Request(
            endpoint,
            data=json.dumps({
                "version_indicator": {
                    "type": "version_ref",
                    "agent_version": self.agent_version,
                },
            }).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FoundryInvocationError("The promoted agent session did not become available.")
            with urllib.request.urlopen(request, timeout=remaining) as response:
                session = json.loads(response.read().decode("utf-8-sig"))
            if not isinstance(session, dict):
                raise FoundryInvocationError("Foundry returned an invalid promoted session.")
            session_id = session.get("agent_session_id")
            indicator = session.get("version_indicator")
            if (
                not isinstance(session_id, str)
                or not session_id
                or not isinstance(indicator, dict)
                or indicator.get("type") != "version_ref"
                or indicator.get("agent_version") != self.agent_version
            ):
                raise FoundryInvocationError("Foundry did not bind the promoted agent version.")
            if session.get("status") in {"active", "idle"}:
                return session_id
            if session.get("status") not in {"creating", "updating"}:
                raise FoundryInvocationError("The promoted agent session is unavailable.")
            time.sleep(min(1, max(0, deadline - time.monotonic())))
            request = urllib.request.Request(
                f"{endpoint.split('?', 1)[0]}/{quote(session_id, safe='')}?api-version=v1",
                headers=headers,
                method="GET",
            )

    async def start_background(self, *, agent_name: str, prompt: str) -> HostedResponseStart:
        return await asyncio.to_thread(
            self._start_background_sync,
            agent_name=agent_name,
            prompt=prompt,
        )

    def _start_background_sync(self, *, agent_name: str, prompt: str) -> HostedResponseStart:
        try:
            credential = DefaultAzureCredential()
            try:
                token = credential.get_token(AI_SCOPE).token
            finally:
                credential.close()

            endpoint = (
                f"{self.project_endpoint}/agents/{quote(agent_name, safe='')}"
                "/endpoint/protocols/openai/responses"
                f"?api-version={quote(self.api_version, safe='')}"
            )
            headers = {
                "Authorization": f"Bearer {token}",
                "Foundry-Features": FOUNDRY_FEATURES,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            inject(headers)
            payload: dict[str, str | bool] = {
                "input": prompt,
                "store": True,
                "background": True,
            }
            if self.agent_version is not None:
                payload["agent_session_id"] = self._create_pinned_session(
                    agent_name=agent_name,
                    headers=headers,
                )
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if self.agent_version is not None and response.headers.get(
                    "x-agent-session-id"
                ) != payload["agent_session_id"]:
                    raise FoundryInvocationError(
                        "Foundry did not execute the promoted agent session."
                    )
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise FoundryInvocationError(
                f"Foundry rejected hosted response startup with HTTP {exc.code}."
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise FoundryInvocationError(
                "Foundry hosted response startup was unavailable."
            ) from exc
        except AzureError as exc:
            raise FoundryInvocationError(
                "The Waypoint identity could not acquire a Foundry access token."
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FoundryInvocationError(
                "Foundry returned an invalid hosted response payload."
            ) from exc

        if not isinstance(body, dict):
            raise FoundryInvocationError("Foundry returned an invalid hosted response payload.")
        response_id = body.get("id") or body.get("response_id")
        if not isinstance(response_id, str) or not response_id:
            raise FoundryInvocationError("Foundry returned no hosted response ID.")
        return HostedResponseStart(
            response_id=response_id,
            status=str(body.get("status") or "in_progress"),
        )
