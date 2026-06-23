"""Schema for ext-bedrock-traces. AgentCore OTEL via CloudWatch / ADOT."""

from __future__ import annotations

from pydantic import Field

from schemas.base import RawRecord


class RawBedrockTrace(RawRecord):
    span_id: str
    trace_id: str | None = None
    timestamp: str | None = None
    agent_endpoint_id: str | None = None
    session_id: str | None = None
    gen_ai_agent_id: str | None = None
    tool_invocations: list[str] = Field(default_factory=list)
    latency_ms: float | None = None
    token_count: int | None = None
    error_type: str | None = None
