"""Schema for ext-foundry-metrics. Azure Monitor (CognitiveServices accounts)."""

from __future__ import annotations

from schemas.base import RawRecord


class RawFoundryMetric(RawRecord):
    metric_name: str
    timestamp: str
    value: float | None = None
    model_deployment_name: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    region: str | None = None
    api_name: str | None = None
    status_code: str | None = None
