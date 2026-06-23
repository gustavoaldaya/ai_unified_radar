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
        status, body, _ = self._post_json(
            url, token, {"type": "ActualCost", "timeframe": "MonthToDate"}
        )
        if status != 200:
            raise RuntimeError(f"Cost Management returned HTTP {status}")
        props = body.get("properties", {})
        yield list(props.get("rows", []))
