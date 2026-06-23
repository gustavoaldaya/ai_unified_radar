"""Schema for ext-bedrock-invocations. CloudWatch Logs ModelInvocationLog v1.0."""

from __future__ import annotations

from schemas.base import RawRecord


class RawBedrockInvocation(RawRecord):
    request_id: str
    timestamp: str | None = None
    schema_type: str | None = None  # ModelInvocationLog v1.0
    account_id: str | None = None
    identity_arn: str | None = None
    region: str | None = None
    operation: str | None = None  # InvokeModel / Converse / InvokeAgent
    model_id: str | None = None
    input_token_count: int | None = None
    output_token_count: int | None = None
