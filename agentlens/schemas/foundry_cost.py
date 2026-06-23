"""Schema for ext-foundry-cost. Azure Cost Management, FOCUS schema."""

from __future__ import annotations

from pydantic import Field

from schemas.base import RawRecord


class RawFoundryCost(RawRecord):
    resource_id: str
    charge_period_start: str
    billed_cost: float | None = None
    effective_cost: float | None = None
    consumed_quantity: float | None = None
    consumed_unit: str | None = None  # tokens / PTU-hours
    meter_category: str | None = None
    meter_name: str | None = None
    subscription_id: str | None = None
    resource_group: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
