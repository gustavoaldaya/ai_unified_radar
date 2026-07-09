"""``ext-foundry-traces``. Azure AI Foundry OTEL spans via Log Analytics (KQL).

The gen_ai.* spans emitted by the Foundry / Azure AI Inference SDKs land in the
workspace-based Application Insights table ``AppDependencies`` (spans map to
dependencies; ``AppTraces`` carries gen_ai *events*, i.e. message contents,
which we do not ingest). The dynamic ``Properties`` bag holds the semantic
convention attributes.

The KQL projects DIRECTLY onto the :class:`RawFoundryTrace` field names so the
downstream validate step maps 1:1 (no Python-side renaming). Token attribute
names changed in the 2024 revision of the gen_ai conventions
(``prompt_tokens``/``completion_tokens`` -> ``input_tokens``/``output_tokens``);
we coalesce both spellings.

Verification: run ``uv run python star/discover_foundry_traces.py`` first --
it executes this exact production KQL (plus a schema census) against the live
workspace, so the query is validated before the extractor writes anything.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from extractors.base import BaseExtractor, Page
from extractors.core.azure_http import LOG_ANALYTICS_SCOPE, AzureJsonSource
from schemas.foundry_traces import RawFoundryTrace

# First capture (no watermark yet) reaches back this far; the default Log
# Analytics retention is 30 days, so this is "everything available".
_FIRST_CAPTURE_LOOKBACK = "30d"


def build_kql(since: str | None) -> str:
    """Production KQL. ``since`` is the watermark cursor (ISO date) or None.

    Notes on the projection:
      * ``Id``/``OperationId`` are the span/trace ids in workspace-based AI.
      * ``gen_ai.request.model`` carries the *deployment* name on Azure OpenAI;
        ``gen_ai.response.model`` carries the underlying model id.
      * ``gen_ai.agent.id`` is emitted by the Foundry Agents service;
        ``gen_ai.assistant.id`` was the pre-GA assistants spelling.
      * caller identity, when present, travels in the ``Properties`` bag as
        ``user.id`` (current semconv; carries Entra object ids) -- the native
        ``User*`` columns are empty in this workspace (probe_caller 2026-07-09).
        Bag keys go FIRST in the coalesce: the native columns yield '' (non
        null) and would shadow the bag otherwise.
      * tostring() on a missing dynamic key yields '' (not null); the star
        loader's ``_text`` treats '' as NULL downstream, so no special-casing.
    """
    window = (
        f"TimeGenerated > todatetime('{since}')"
        if since
        else f"TimeGenerated > ago({_FIRST_CAPTURE_LOOKBACK})"
    )
    return f"""
AppDependencies
| where {window}
| where isnotempty(Id)
| where isnotempty(Properties["gen_ai.system"])
      or isnotempty(Properties["gen_ai.agent.id"])
      or tostring(Properties["gen_ai.operation.name"]) in ("invoke_agent", "create_agent")
| project
    span_id           = tostring(Id),
    trace_id          = tostring(OperationId),
    parent_span_id    = tostring(ParentId),
    timestamp         = tostring(TimeGenerated),
    caller_id         = tostring(coalesce(
                            Properties["user.id"],
                            Properties["enduser.id"],
                            column_ifexists("UserAuthenticatedId", ""),
                            column_ifexists("UserId", ""))),
    gen_ai_agent_id   = tostring(coalesce(
                            Properties["gen_ai.agent.id"],
                            Properties["gen_ai.assistant.id"])),
    gen_ai_agent_name = tostring(Properties["gen_ai.agent.name"]),
    span_kind         = tostring(coalesce(
                            Properties["gen_ai.operation.name"], Name)),
    model_name        = tostring(coalesce(
                            Properties["gen_ai.response.model"],
                            Properties["gen_ai.request.model"])),
    deployment_name   = tostring(Properties["gen_ai.request.model"]),
    prompt_tokens     = toint(coalesce(
                            Properties["gen_ai.usage.input_tokens"],
                            Properties["gen_ai.usage.prompt_tokens"])),
    completion_tokens = toint(coalesce(
                            Properties["gen_ai.usage.output_tokens"],
                            Properties["gen_ai.usage.completion_tokens"])),
    total_tokens_raw  = toint(Properties["gen_ai.usage.total_tokens"]),
    latency_ms        = todouble(DurationMs),
    status_code       = tostring(ResultCode)
| extend total_tokens = coalesce(
    total_tokens_raw,
    coalesce(prompt_tokens, 0) + coalesce(completion_tokens, 0))
| project-away total_tokens_raw
""".strip()


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
        status, body, _ = self._post_json(url, token, {"query": build_kql(since)})
        if status != 200:
            raise RuntimeError(f"Log Analytics returned HTTP {status}: {body}")
        yield _rows_from_tables(body)


def _rows_from_tables(body: dict[str, Any]) -> Page:  # pragma: no cover - network
    rows: Page = []
    for table in body.get("tables", []):
        columns = [c["name"] for c in table.get("columns", [])]
        for values in table.get("rows", []):
            rows.append(dict(zip(columns, values, strict=False)))
    return rows
