"""Schema for ext-foundry-traces. App Insights OTEL (gen_ai.* conventions)."""

from __future__ import annotations

from pydantic import Field

from schemas.base import RawRecord


class RawFoundryTrace(RawRecord):
    span_id: str
    trace_id: str | None = None
    timestamp: str | None = None
    gen_ai_agent_id: str | None = None
    gen_ai_agent_name: str | None = None
    span_kind: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    deployment_name: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: float | None = None
    status_code: str | None = None
    tool_calls: list[str] = Field(default_factory=list)
