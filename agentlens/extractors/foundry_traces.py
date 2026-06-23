"""``ext-foundry-traces``. Azure AI Foundry OTEL spans via Log Analytics (KQL)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from extractors.base import BaseExtractor, Page
from extractors.core.azure_http import LOG_ANALYTICS_SCOPE, AzureJsonSource
from schemas.foundry_traces import RawFoundryTrace

_KQL = "AppTraces | where TimeGenerated > ago(1h)"


class FoundryTracesExtractor(AzureJsonSource, BaseExtractor):
    name = "ext-foundry-traces"
    schema = RawFoundryTrace
    source_path = "foundry/traces"
    source_id_field = "span_id"
    timestamp_field = "timestamp"
    agent_id_field = "gen_ai_agent_id"

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        token = self._aad_token(LOG_ANALYTICS_SCOPE)
        workspace = os.environ["LOG_ANALYTICS_WORKSPACE_ID"]
        url = f"https://api.loganalytics.io/v1/workspaces/{workspace}/query"
        self.rate_limit().before_request()
        status, body, _ = self._post_json(url, token, {"query": _KQL})
        if status != 200:
            raise RuntimeError(f"Log Analytics returned HTTP {status}")
        yield _rows_from_tables(body)


def _rows_from_tables(body: dict[str, Any]) -> Page:  # pragma: no cover - network
    rows: Page = []
    for table in body.get("tables", []):
        columns = [c["name"] for c in table.get("columns", [])]
        for values in table.get("rows", []):
            rows.append(dict(zip(columns, values, strict=False)))
    return rows
