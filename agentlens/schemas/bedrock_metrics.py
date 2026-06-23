"""Schema for ext-bedrock-metrics. CloudWatch GetMetricData (AWS/Bedrock)."""

from __future__ import annotations

from schemas.base import RawRecord


class RawBedrockMetric(RawRecord):
    metric_name: str
    timestamp: str
    value: float | None = None
    model_id: str | None = None
    operation_name: str | None = None
    namespace: str | None = None  # AWS/Bedrock or Bedrock-AgentCore
