"""OneLake (Microsoft Fabric) corpus access for Waypoint.

Resolves contract/policy/invoice document content from a Fabric Lakehouse ``Files`` area using the
ADLS Gen2 (``azure-storage-file-datalake``) API against the OneLake DFS endpoint with
``DefaultAzureCredential`` (the container-app managed identity in Azure).

All access degrades gracefully: when no lake is configured, the Azure SDK is unavailable, or a file
is missing, reads return ``None`` instead of raising so callers can fall back to URI-only metadata
(the pre-OneLake behavior).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .settings import Settings
from .tracer import trace_span

logger = logging.getLogger(__name__)

# Token scope for the Fabric control-plane REST API, used to resolve a lakehouse display name to its
# GUID. The Waypoint container-app managed identity is a Fabric workspace Member, which can list
# lakehouses.
_FABRIC_API_SCOPE = "https://api.fabric.microsoft.com/.default"
_FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _is_guid(value: str) -> bool:
    return bool(_GUID_RE.match(value.strip()))


# Lakehouse-relative subdirectories for each corpus document category. Combined with the configured
# corpus prefix (default ``Files/corpus``) to form a full lakehouse-relative path. This layout is
# the interface the Ledgerfield session uploads against.
_CATEGORY_SUBDIRS: dict[str, str] = {
    "contract": "contracts",
    "policy": "policies",
    "invoice-html": "invoices/html",
    "invoice-pdf": "invoices/pdf",
    "evidence": "evidence",
}

# Content source markers surfaced to API responses.
SOURCE_ONELAKE = "onelake"
SOURCE_URI_ONLY = "uri-only"


@dataclass(frozen=True)
class ResolvedDocument:
    """The outcome of resolving a stored document URI against the corpus lake."""

    text: str | None
    content_source: str
    path: str | None = None


@dataclass(frozen=True)
class ResolvedBytes:
    """The outcome of resolving a stored document URI to raw bytes from the corpus lake."""

    data: bytes | None
    content_source: str
    path: str | None = None


def _basename(uri: str) -> str:
    """Return the final path segment of a URI or path-like string."""

    cleaned = uri.split("?", 1)[0].rstrip("/")
    for separator in ("://", ":/"):
        if separator in cleaned:
            cleaned = cleaned.split(separator, 1)[1]
    return cleaned.rsplit("/", 1)[-1]


def resolve_corpus_path(uri: str | None, category: str, corpus_prefix: str) -> str | None:
    """Map a stored document URI to a lakehouse-relative OneLake path.

    Returns ``None`` when the URI is empty. ``onelake://`` URIs and already lakehouse-relative
    ``Files/`` paths pass through unchanged; everything else is placed under the corpus prefix using
    the document ``category`` and the URI basename.
    """

    if not uri:
        return None

    normalized = uri.strip()
    if normalized.startswith("onelake://"):
        return normalized[len("onelake://") :].lstrip("/")
    if normalized.startswith("Files/"):
        return normalized

    subdir = _CATEGORY_SUBDIRS.get(category, category)
    prefix = corpus_prefix.strip("/")
    return f"{prefix}/{subdir}/{_basename(normalized)}"


class OneLakeClient:
    """Thin, lazily-initialized reader over a Fabric Lakehouse ``Files`` area."""

    def __init__(self, settings: Settings) -> None:
        self._account_url = settings.onelake_account_url
        self._workspace = settings.onelake_workspace
        self._lakehouse = settings.onelake_lakehouse
        self._corpus_prefix = settings.onelake_corpus_prefix
        self._local_root = (
            Path(settings.corpus_local_root).resolve() if settings.corpus_local_root else None
        )
        self._service_client: object | None = None
        self._unavailable = False
        self._lakehouse_segment: str | None = None

    @property
    def configured(self) -> bool:
        """True when a workspace and lakehouse are configured for OneLake access."""

        return bool(self._workspace and self._lakehouse)

    @property
    def corpus_prefix(self) -> str:
        return self._corpus_prefix

    def _filesystem_client(self) -> object | None:
        """Return the cached ADLS filesystem client for the workspace, or ``None``."""

        if not self.configured or self._unavailable:
            return None

        if self._service_client is None:
            try:
                from azure.identity import DefaultAzureCredential
                from azure.storage.filedatalake import DataLakeServiceClient
            except ImportError:
                logger.warning(
                    "OneLake is configured but azure-storage-file-datalake/azure-identity are "
                    "not installed; falling back to URI-only document metadata."
                )
                self._unavailable = True
                return None

            self._service_client = DataLakeServiceClient(
                account_url=self._account_url,
                credential=DefaultAzureCredential(),
            )

        service_client = self._service_client
        return service_client.get_file_system_client(self._workspace)  # type: ignore[attr-defined]

    def _resolve_lakehouse_segment(self) -> str:
        """Return the OneLake path segment for the configured lakehouse, resolved once and cached.

        OneLake DFS paths accept either a friendly ``<name>.Lakehouse`` segment or a bare lakehouse
        GUID. Many tenants disable friendly-name support (the DFS endpoint then returns
        ``FriendlyNameSupportDisabled``), and a GUID lakehouse must NOT carry the ``.Lakehouse``
        suffix (the endpoint rejects ``<guid>.Lakehouse``). This mirrors the Ledgerfield uploader's
        ``_resolve_lakehouse_segment`` so reader and writer agree on the path.

        Resolution order:
          1. If the lakehouse is already a GUID, use the bare GUID.
          2. Otherwise look it up by ``displayName`` via the Fabric REST API (requires the workspace
             to be a GUID, which it is when provisioned by Waypoint).
          3. If lookup fails, fall back to the friendly ``<name>.Lakehouse`` segment.
        """

        if self._lakehouse_segment is not None:
            return self._lakehouse_segment

        lakehouse = self._lakehouse
        segment = f"{lakehouse}.Lakehouse"

        if _is_guid(lakehouse):
            segment = lakehouse
        elif _is_guid(self._workspace):
            lakehouse_id = self._lookup_lakehouse_id(self._workspace, lakehouse)
            if lakehouse_id:
                logger.info("Resolved OneLake lakehouse '%s' -> %s", lakehouse, lakehouse_id)
                segment = lakehouse_id
            else:
                logger.warning(
                    "Could not resolve OneLake lakehouse '%s' to a GUID; using friendly-name path.",
                    lakehouse,
                )
        else:
            logger.warning(
                "OneLake workspace '%s' is not a GUID; cannot resolve lakehouse GUID via Fabric "
                "REST. Using friendly-name path.",
                self._workspace,
            )

        self._lakehouse_segment = segment
        return segment

    def _lookup_lakehouse_id(self, workspace: str, lakehouse: str) -> str | None:
        """Look up a lakehouse GUID by display name within a workspace (Fabric REST)."""

        import json
        import urllib.request

        try:
            from azure.identity import DefaultAzureCredential
        except ImportError:
            logger.warning(
                "OneLake lakehouse GUID resolution needs azure-identity, which is not installed; "
                "using friendly-name path."
            )
            return None

        try:
            token = DefaultAzureCredential().get_token(_FABRIC_API_SCOPE).token
        except Exception as exc:  # noqa: BLE001 - graceful degradation on any auth failure
            logger.warning("Could not acquire a Fabric API token for lakehouse resolution: %s", exc)
            return None

        headers = {"Authorization": f"Bearer {token}"}
        url: str | None = f"{_FABRIC_API_BASE}/workspaces/{workspace}/lakehouses"
        target = lakehouse.strip().casefold()

        try:
            while url:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - trusted Fabric URL
                    body = json.loads(resp.read().decode("utf-8"))
                for item in body.get("value", []):
                    if str(item.get("displayName", "")).strip().casefold() == target:
                        return item.get("id")
                url = body.get("continuationUri") or None
        except Exception as exc:  # noqa: BLE001 - graceful degradation on any REST failure
            logger.warning("Fabric REST lakehouse lookup failed for '%s': %s", lakehouse, exc)
            return None

        return None

    def read_text(self, lakehouse_relative_path: str) -> str | None:
        """Read a UTF-8 document from the lakehouse, or ``None`` when unavailable/missing."""

        data = self.read_bytes(lakehouse_relative_path)
        if data is None:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("OneLake file %s is not valid UTF-8 text", lakehouse_relative_path)
            return None

    def read_bytes(self, lakehouse_relative_path: str) -> bytes | None:
        """Read raw bytes from the lakehouse, or ``None`` when unavailable/missing."""

        filesystem = self._filesystem_client()
        if filesystem is None:
            return None

        file_path = f"{self._resolve_lakehouse_segment()}/{lakehouse_relative_path.lstrip('/')}"
        with trace_span(
            "onelake_read_bytes",
            attributes={"onelake.path": file_path, "onelake.workspace": self._workspace},
        ) as span:
            try:
                file_client = filesystem.get_file_client(file_path)  # type: ignore[attr-defined]
                downloaded = file_client.download_file()
                data: bytes = downloaded.readall()
                span.set_attribute("onelake.found", True)
                return data
            except Exception as exc:  # noqa: BLE001 - graceful degradation on any read failure
                span.set_attribute("onelake.found", False)
                logger.info("OneLake read miss for %s: %s", file_path, exc)
                return None

    def resolve_document(self, uri: str | None, category: str) -> ResolvedDocument:
        """Resolve a stored document URI to its text content from the corpus lake."""

        if self._local_root is not None:
            local_path = self._local_path(uri)
            if local_path is None or not local_path.is_file():
                return ResolvedDocument(text=None, content_source=SOURCE_URI_ONLY, path=uri)
            return ResolvedDocument(
                text=local_path.read_text(encoding="utf-8"), content_source="local-corpus", path=uri
            )
        path = resolve_corpus_path(uri, category, self._corpus_prefix)
        if path is None or not self.configured:
            return ResolvedDocument(text=None, content_source=SOURCE_URI_ONLY, path=path)

        text = self.read_text(path)
        if text is None:
            return ResolvedDocument(text=None, content_source=SOURCE_URI_ONLY, path=path)
        return ResolvedDocument(text=text, content_source=SOURCE_ONELAKE, path=path)

    def resolve_document_bytes(self, uri: str | None, category: str) -> ResolvedBytes:
        """Resolve a stored document URI to its raw bytes from the corpus lake."""

        if self._local_root is not None:
            local_path = self._local_path(uri)
            if local_path is None or not local_path.is_file():
                return ResolvedBytes(data=None, content_source=SOURCE_URI_ONLY, path=uri)
            return ResolvedBytes(
                data=local_path.read_bytes(), content_source="local-corpus", path=uri
            )
        path = resolve_corpus_path(uri, category, self._corpus_prefix)
        if path is None or not self.configured:
            return ResolvedBytes(data=None, content_source=SOURCE_URI_ONLY, path=path)

        data = self.read_bytes(path)
        if data is None:
            return ResolvedBytes(data=None, content_source=SOURCE_URI_ONLY, path=path)
        return ResolvedBytes(data=data, content_source=SOURCE_ONELAKE, path=path)

    def _local_path(self, uri: str | None) -> Path | None:
        if not uri or self._local_root is None or "://" in uri:
            return None
        relative = Path(uri)
        if relative.is_absolute() or relative.parts[:1] != ("data",):
            return None
        path = (self._local_root / relative).resolve()
        if not path.is_relative_to(self._local_root / "data"):
            return None
        return path


_client_cache: dict[tuple[str, str, str, str, str], OneLakeClient] = {}


def get_onelake_client(settings: Settings) -> OneLakeClient:
    """Return a cached :class:`OneLakeClient` for the current OneLake configuration."""

    cache_key = (
        settings.onelake_account_url,
        settings.onelake_workspace,
        settings.onelake_lakehouse,
        settings.onelake_corpus_prefix,
        settings.corpus_local_root,
    )
    client = _client_cache.get(cache_key)
    if client is None:
        client = OneLakeClient(settings)
        _client_cache[cache_key] = client
    return client
