"""``ext-foundry-metrics``. Azure Monitor metrics for CognitiveServices accounts."""

from __future__ import annotations

import os
from collections.abc import Iterator

from extractors.base import BaseExtractor, Page
from extractors.core.azure_http import ARM_SCOPE, AzureJsonSource
from extractors.core.dedup import dedup_key
from schemas.base import RawRecord
from schemas.foundry_metrics import RawFoundryMetric


class FoundryMetricsExtractor(AzureJsonSource, BaseExtractor):
    name = "ext-foundry-metrics"
    schema = RawFoundryMetric
    source_path = "foundry/metrics"
    source_id_field = "metric_name"
    timestamp_field = "timestamp"

    def _dedup_key(self, record: RawRecord) -> str:
        parts = [
            str(getattr(record, "metric_name", "")),
            str(getattr(record, "model_deployment_name", "")),
        ]
        return dedup_key(":".join(parts), str(getattr(record, "timestamp", "")))

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        token = self._aad_token(ARM_SCOPE)
        resource_id = os.environ["FOUNDRY_ACCOUNT_RESOURCE_ID"]
        url = (
            f"https://management.azure.com{resource_id}"
            "/providers/microsoft.insights/metrics?api-version=2023-10-01"
        )
        self.rate_limit().before_request()
        status, body, _ = self._get_json(url, token)
        if status != 200:
            raise RuntimeError(f"Azure Monitor returned HTTP {status}")
        yield list(body.get("value", []))
