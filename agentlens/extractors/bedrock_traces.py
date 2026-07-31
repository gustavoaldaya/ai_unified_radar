"""``ext-bedrock-traces``. Spans OTEL de AgentCore desde CloudWatch ``aws/spans``.

Fuente (corregida 2026-07-14): los spans OTEL de los runtimes AgentCore NO
viven en ``/aws/bedrock-agentcore/traces`` (ese log group no existe en la
cuenta). AWS Application Signals los deposita, ya aplanados, en el log group
``aws/spans`` -- un registro JSON por span con ``traceId``/``spanId``/``name``/
``kind``/``attributes``/``resource``. El colector OTel del stack ``bedrock-otel``
(NLB :4317 -> ECS) es solo el lado de INGESTA (push); ``aws/spans`` es el store
consultable via ``logs:FilterLogEvents``.

Mapeo OTel span -> ``RawBedrockTrace`` (ver ``_map_span``):
  * identidad de agente desde ``resource.attributes``: ``cloud.resource_id``
    (ARN del runtime-endpoint) -> ``agent_endpoint_id``; ``service.name`` ->
    ``gen_ai_agent_id``.
  * ``startTimeUnixNano`` -> ``timestamp`` (ISO-8601); ``durationNano`` ->
    ``latency_ms``.
  * spans gen_ai enriquecidos cuando traen ``gen_ai.usage.*`` (``token_count``),
    ``gen_ai.tool.name`` (``tool_invocations``) o ``status.code=ERROR`` /
    ``http.*_status_code>=400`` (``error_type``).
  * el resto del span (name, kind, parentSpanId, atributos, resource) se
    conserva en la columna ``_drift`` sin tocar el esquema.

Permiso IAM: ``logs:FilterLogEvents`` sobre ``aws/spans``. Si el log group no
existe (Application Signals sin habilitar), run vacio (estado esperado, no
fallo). Override del grupo via ``BEDROCK_SPANS_LOG_GROUP``; ventana inicial via
``BEDROCK_SPANS_LOOKBACK_DAYS`` (default 30, retencion tipica del grupo).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from extractors.base import BaseExtractor, Page
from extractors.core.aws_client import AwsClientSource
from schemas.bedrock_traces import RawBedrockTrace

_DEFAULT_LOG_GROUP = "aws/spans"
_DEFAULT_LOOKBACK_DAYS = 30


def _iso_from_nano(nano: object) -> str | None:
    """Epoch en nanosegundos (OTEL) -> ISO-8601 UTC."""
    try:
        return datetime.fromtimestamp(int(nano) / 1e9, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _resource_attrs(span: dict) -> dict:
    """``resource`` puede venir como ``{"attributes": {...}}`` o ya aplanado."""
    res = span.get("resource") or {}
    if isinstance(res, dict):
        attrs = res.get("attributes")
        return attrs if isinstance(attrs, dict) else res
    return {}


def _token_count(attrs: dict) -> int | None:
    for key in ("gen_ai.usage.total_tokens", "llm.usage.total_tokens"):
        if key in attrs:
            try:
                return int(attrs[key])
            except (TypeError, ValueError):
                pass
    inp = attrs.get("gen_ai.usage.input_tokens", attrs.get("gen_ai.usage.prompt_tokens"))
    out = attrs.get(
        "gen_ai.usage.output_tokens", attrs.get("gen_ai.usage.completion_tokens")
    )
    if inp is None and out is None:
        return None
    try:
        return int(inp or 0) + int(out or 0)
    except (TypeError, ValueError):
        return None


def _error_type(span: dict, attrs: dict) -> str | None:
    status = span.get("status") or {}
    if isinstance(status, dict) and str(status.get("code")).upper() in (
        "ERROR",
        "STATUS_CODE_ERROR",
    ):
        return status.get("message") or attrs.get("exception.type") or "ERROR"
    code = attrs.get("http.response.status_code") or attrs.get("http.status_code")
    try:
        if code is not None and int(code) >= 400:
            return f"HTTP {int(code)}"
    except (TypeError, ValueError):
        pass
    exc = attrs.get("exception.type")
    return str(exc) if exc else None


def _map_span(span: dict) -> dict | None:
    """Span OTEL (``aws/spans``, aplanado) -> dict del esquema RawBedrockTrace.

    Devuelve ``None`` si el registro no es un span (sin ``spanId``). Los campos
    no declarados en el esquema se conservan como drift (columna ``_drift``)."""
    span_id = span.get("spanId") or span.get("span_id")
    if not span_id:
        return None
    attrs = span.get("attributes")
    attrs = attrs if isinstance(attrs, dict) else {}
    res = _resource_attrs(span)
    duration_nano = span.get("durationNano")
    latency_ms = (
        float(duration_nano) / 1e6 if isinstance(duration_nano, (int, float)) else None
    )
    tool = attrs.get("gen_ai.tool.name") or attrs.get("tool.name")
    return {
        # --- campos declarados del esquema ---
        "span_id": str(span_id),
        "trace_id": span.get("traceId") or span.get("trace_id"),
        "timestamp": _iso_from_nano(span.get("startTimeUnixNano")),
        "agent_endpoint_id": res.get("cloud.resource_id") or res.get("service.name"),
        "session_id": (
            attrs.get("session.id")
            or attrs.get("gen_ai.conversation.id")
            or attrs.get("aws.bedrock.agentcore.session.id")
        ),
        "gen_ai_agent_id": (
            attrs.get("gen_ai.agent.id")
            or attrs.get("gen_ai.agent.name")
            or res.get("service.name")
        ),
        "tool_invocations": [str(tool)] if tool else [],
        "latency_ms": latency_ms,
        "token_count": _token_count(attrs),
        "error_type": _error_type(span, attrs),
        # --- extras -> _drift (fidelidad sin cambiar el esquema) ---
        "span_name": span.get("name"),
        "span_kind": span.get("kind"),
        "parent_span_id": span.get("parentSpanId"),
        "service_name": res.get("service.name"),
        "cloud_provider": res.get("cloud.provider"),
        "cloud_region": res.get("cloud.region"),
        "aws_service_type": res.get("aws.service.type"),
        "gen_ai_request_model": attrs.get("gen_ai.request.model"),
        "span_attributes": attrs,
    }


class BedrockTracesExtractor(AwsClientSource, BaseExtractor):
    name = "ext-bedrock-traces"
    schema = RawBedrockTrace
    source_path = "bedrock/traces"
    source_id_field = "span_id"
    timestamp_field = "timestamp"
    agent_id_field = "agent_endpoint_id"

    def _start_time_ms(self, since: str | None) -> int:
        """Floor de la ventana en epoch-ms: watermark si existe, si no lookback."""
        if since:
            try:
                dt = datetime.fromisoformat(since)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                pass
        days = int(
            os.environ.get("BEDROCK_SPANS_LOOKBACK_DAYS", _DEFAULT_LOOKBACK_DAYS)
        )
        floor = datetime.now(timezone.utc) - timedelta(days=days)
        return int(floor.timestamp() * 1000)

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        client = self._aws_client("logs")
        log_group = os.environ.get("BEDROCK_SPANS_LOG_GROUP", _DEFAULT_LOG_GROUP)
        start_time = self._start_time_ms(since)
        token: str | None = None
        while True:
            kwargs: dict = {
                "logGroupName": log_group,
                "startTime": start_time,
                "limit": 1000,
            }
            if token:
                kwargs["nextToken"] = token
            self.rate_limit().before_request()
            try:
                resp = client.filter_log_events(**kwargs)
            except client.exceptions.ResourceNotFoundException:
                # aws/spans ausente = Application Signals sin habilitar:
                # estado esperado, no un fallo -> run vacio.
                return
            page: Page = []
            for event in resp.get("events", []):
                message = event.get("message")
                if not message:
                    continue
                try:
                    span = json.loads(message)
                except (TypeError, ValueError):
                    continue
                record = _map_span(span)
                if record is not None:
                    page.append(record)
            if page:
                yield page
            token = resp.get("nextToken")
            if not token:
                break
