"""``ext-foundry-cost``. Azure Cost Management query (FOCUS schema).

Cost Management has a hard ~15 reads/hour quota; the default rate limiter raises
instead of retrying inside the backoff window (inherited FinOps lesson). Prefer
scheduled FOCUS exports to ADLS in production; this API-pull path is the fallback.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from extractors.base import BaseExtractor, Page
from extractors.core.azure_http import ARM_SCOPE, AzureJsonSource
from extractors.core.config import Settings
from extractors.core.dedup import dedup_key
from extractors.core.ratelimit import RateLimiter
from extractors.core.storage import StorageBackend
from schemas.base import RawRecord
from schemas.foundry_cost import RawFoundryCost

# Cost Management Query API body. The ``dataset`` block (granularity +
# aggregation) is required; its absence is the "Dataset invalid" HTTP 400.
# Grouping is capped at two dimensions by the API, so we take ResourceId + Meter
# and derive subscription / resource group from the resource id string.
_QUERY_BODY = {
    "type": "ActualCost",
    "timeframe": "MonthToDate",
    "dataset": {
        "granularity": "Daily",
        "aggregation": {
            "totalCost": {"name": "PreTaxCost", "function": "Sum"},
            "usageQuantity": {"name": "UsageQuantity", "function": "Sum"},
        },
        "grouping": [
            {"type": "Dimension", "name": "ResourceId"},
            {"type": "Dimension", "name": "Meter"},
        ],
    },
}


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _usage_date_to_iso(value: object) -> str | None:
    """Cost Management returns the Daily bucket as an int ``yyyymmdd``."""
    if value is None:
        return None
    digits = str(int(value)) if isinstance(value, (int, float)) else str(value)
    if len(digits) != 8:
        return None
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"


def _parse_scope(resource_id: str) -> tuple[str | None, str | None]:
    """Pull subscriptionId / resourceGroup out of an ARM resource id."""
    parts = resource_id.strip("/").split("/")
    lower = [p.lower() for p in parts]
    sub = rg = None
    if "subscriptions" in lower:
        i = lower.index("subscriptions")
        sub = parts[i + 1] if i + 1 < len(parts) else None
    if "resourcegroups" in lower:
        i = lower.index("resourcegroups")
        rg = parts[i + 1] if i + 1 < len(parts) else None
    return sub, rg


class FoundryCostExtractor(AzureJsonSource, BaseExtractor):
    name = "ext-foundry-cost"
    schema = RawFoundryCost
    source_path = "foundry/cost"
    source_id_field = "resource_id"
    timestamp_field = "charge_period_start"

    def __init__(
        self,
        settings: Settings | None = None,
        backend: StorageBackend | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        super().__init__(settings, backend, rate_limiter)
        if rate_limiter is None:  # Cost Management ~15 reads/h hard limit
            self.rate_limiter = RateLimiter(
                max_per_hour=15, no_retry_on_hard_limit=True
            )

    def _dedup_key(self, record: RawRecord) -> str:
        parts = [
            str(getattr(record, "resource_id", "")),
            str(getattr(record, "meter_name", "")),
        ]
        return dedup_key(
            ":".join(parts), str(getattr(record, "charge_period_start", ""))
        )

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        token = self._aad_token(ARM_SCOPE)
        scope = os.environ["AZURE_COST_SCOPE"]
        url = (
            f"https://management.azure.com{scope}"
            "/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
        )
        self.rate_limit().before_request()
        status, body, retry_after = self._post_json(url, token, _QUERY_BODY)
        if status == 429:
            hint = f" Retry-After={retry_after:.0f}s." if retry_after else ""
            raise RuntimeError(
                "Cost Management HTTP 429 -- hourly hard quota (~15 reads/h) "
                f"exhausted.{hint} Do NOT batch-retry; run this extractor "
                "alone after the hourly reset."
            )
        if status != 200:
            raise RuntimeError(f"Cost Management returned HTTP {status}: {body}")
        props = body.get("properties", {})
        yield list(self._rows_to_records(props))

    def _rows_to_records(self, props: dict) -> Iterator[dict]:
        """Cost Management returns positional ``rows`` aligned to ``columns``
        metadata. Zip them into dicts mapped onto the FOCUS schema fields."""
        columns = [col.get("name") for col in props.get("columns", [])]
        index = {name: i for i, name in enumerate(columns) if name is not None}

        def cell(row: list, name: str) -> object:
            i = index.get(name)
            return row[i] if i is not None and i < len(row) else None

        for row in props.get("rows", []):
            resource_id = cell(row, "ResourceId")
            usage_date = _usage_date_to_iso(cell(row, "UsageDate"))
            if resource_id is None or usage_date is None:
                continue
            cost = _as_float(cell(row, "PreTaxCost"))
            sub, rg = _parse_scope(str(resource_id))
            record = {
                "resource_id": str(resource_id),
                "charge_period_start": usage_date,
                "billed_cost": cost,
                "effective_cost": cost,
                "consumed_quantity": _as_float(cell(row, "UsageQuantity")),
                "meter_name": cell(row, "Meter"),
                "subscription_id": sub,
                "resource_group": rg,
            }
            currency = cell(row, "Currency")
            if currency is not None:
                record["currency"] = currency  # drift signal (not a declared field)
            yield record
