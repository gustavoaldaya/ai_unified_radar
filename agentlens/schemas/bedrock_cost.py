"""Schema for ext-bedrock-cost. Cost Explorer GetCostAndUsage (model-level)."""

from __future__ import annotations

from schemas.base import RawRecord


class RawBedrockCost(RawRecord):
    time_period_start: str
    service: str | None = None
    usage_type: str | None = None  # encodes the model
    operation: str | None = None
    unblended_cost: float | None = None
    usage_quantity: float | None = None
