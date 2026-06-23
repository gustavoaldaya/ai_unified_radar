"""``ext-m365-registry`` (M1.4) -- first concrete extractor.

Extends :class:`BaseExtractor`, reusing the whole pipeline (AC-1.4-1). Only the
live ``paginate()`` is implemented here: a full daily snapshot of the Agent
Registry catalog via Microsoft Graph, with OData ``@odata.nextLink`` pagination
(AC-1.4-2). Under ``USE_FIXTURES`` this method is never called -- the base
harness reads ``tests/fixtures/ext-m365-registry/``.

Live endpoint (frozen M1.4 spec): ``GET /beta/copilot/admin/catalog/packages``.
Empirical caveat (ADR-K23 spike ``spike-agent-registry-graph-fields``, run
2026-06-13 on a production tenant): this Catalog branch is **gated by an
Agent 365 license** -- it returns HTTP 403 *"Customer must be a licensed for
Agent 365 in order to use Agent 365 Graph APIs"* (MC1173195) on tenants without
A365. The ``/beta/agentRegistry/agentInstances`` branch answers 200 with
``Directory.Read.All`` and no A365 gate. We keep the spec endpoint as primary
but (a) make the base URL overridable via ``GRAPH_REGISTRY_URL`` so the cutover
can switch to the un-gated branch, and (b) raise an actionable error on 403.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Iterator
from typing import Any

from extractors.base import BaseExtractor, Page
from schemas.m365_registry import RawAgentPackage

_DEFAULT_CATALOG_URL = (
    "https://graph.microsoft.com/beta/copilot/admin/catalog/packages?$top=100"
)
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class AgentRegistryLicenseError(RuntimeError):
    """Raised on the A365 license gate (HTTP 403) -- see ADR-K23 spike."""


class M365RegistryExtractor(BaseExtractor):
    name = "ext-m365-registry"
    schema = RawAgentPackage
    source_path = "m365-registry"  # raw/m365-registry/dt=.../ (AC-1.4-4)
    source_id_field = "package_id"
    timestamp_field = "last_modified"
    agent_id_field = "package_id"

    # ------------------------------------------------------------------ #
    # Live path (gated on the Service Principal; not run under fixtures)  #
    # ------------------------------------------------------------------ #
    def paginate(self, *, since: str | None) -> Iterator[Page]:
        token = self._access_token()
        limiter = self.rate_limit()
        url: str | None = os.environ.get("GRAPH_REGISTRY_URL", _DEFAULT_CATALOG_URL)
        while url:
            limiter.before_request()
            status, body, retry_after = self._http_get_json(url, token)
            if status == 429:
                limiter.on_response(429, retry_after=retry_after)
                continue
            if status == 403:
                raise AgentRegistryLicenseError(
                    "Graph 403 on the Catalog branch -- tenant likely lacks an "
                    "Agent 365 license (MC1173195). Set GRAPH_REGISTRY_URL to the "
                    "agentRegistry/agentInstances branch (ADR-K23)."
                )
            if status != 200:
                raise RuntimeError(f"Graph returned HTTP {status} for {url}")
            yield list(body.get("value", []))
            url = body.get("@odata.nextLink")

    # ------------------------------------------------------------------ #
    # Seams (overridable in tests so pagination can run without network)  #
    # ------------------------------------------------------------------ #
    def _access_token(self) -> str:
        from azure.identity import DefaultAzureCredential

        return str(DefaultAzureCredential().get_token(_GRAPH_SCOPE).token)

    def _http_get_json(
        self, url: str, token: str
    ) -> tuple[int, dict[str, Any], float | None]:
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
                return response.status, payload, None
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            return exc.code, {}, float(retry_after) if retry_after else None
