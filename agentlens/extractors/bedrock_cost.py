"""``ext-bedrock-cost``. Cost Explorer GetCostAndUsage (model/service level)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

from extractors.base import BaseExtractor, Page
from extractors.core.aws_client import AwsClientSource
from extractors.core.dedup import dedup_key
from schemas.base import RawRecord
from schemas.bedrock_cost import RawBedrockCost


class BedrockCostExtractor(AwsClientSource, BaseExtractor):
    name = "ext-bedrock-cost"
    schema = RawBedrockCost
    source_path = "bedrock/cost"
    source_id_field = "usage_type"
    timestamp_field = "time_period_start"

    def _dedup_key(self, record: RawRecord) -> str:
        parts = [
            str(getattr(record, "usage_type", "")),
            str(getattr(record, "operation", "")),
        ]
        return dedup_key(":".join(parts), str(getattr(record, "time_period_start", "")))

    @staticmethod
    def _time_period(since: str | None) -> dict[str, str]:
        """GetCostAndUsage exige TimePeriod (Start inclusivo, End exclusivo).

        Ventana: desde ``since`` (fecha del watermark) o 30 días atrás, hasta hoy.
        """
        end = date.today()
        start_default = end - timedelta(days=30)
        start = (since or "")[:10] or start_default.isoformat()
        if start >= end.isoformat():
            start = (end - timedelta(days=1)).isoformat()
        return {"Start": start, "End": end.isoformat()}

    @staticmethod
    def _flatten(bucket: dict) -> list[dict]:
        """ResultsByTime -> un registro plano por (día x grupo) (seam live).

        ``Keys`` llega en el orden del GroupBy: [USAGE_TYPE, OPERATION]. Los
        días sin ``Groups`` no aportan registros (sin gasto Bedrock ese día).
        """
        start = (bucket.get("TimePeriod") or {}).get("Start")
        records: list[dict] = []
        for group in bucket.get("Groups", []):
            keys = group.get("Keys") or ["", ""]
            metrics = group.get("Metrics") or {}
            records.append(
                {
                    "time_period_start": start,
                    "service": "Amazon Bedrock",
                    "usage_type": keys[0] if len(keys) > 0 else None,
                    "operation": keys[1] if len(keys) > 1 else None,
                    "unblended_cost": float(
                        (metrics.get("UnblendedCost") or {}).get("Amount") or 0.0
                    ),
                    "usage_quantity": float(
                        (metrics.get("UsageQuantity") or {}).get("Amount") or 0.0
                    ),
                }
            )
        return records

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        client = self._aws_client("ce")
        time_period = self._time_period(since)
        token: str | None = None
        while True:
            kwargs: dict[str, object] = {
                "TimePeriod": time_period,
                "Granularity": "DAILY",
                "Metrics": ["UnblendedCost", "UsageQuantity"],
                "Filter": {
                    "Dimensions": {"Key": "SERVICE", "Values": ["Amazon Bedrock"]}
                },
                "GroupBy": [
                    {"Type": "DIMENSION", "Key": "USAGE_TYPE"},
                    {"Type": "DIMENSION", "Key": "OPERATION"},
                ],
            }
            if token:
                kwargs["NextPageToken"] = token
            self.rate_limit().before_request()
            resp = client.get_cost_and_usage(**kwargs)
            page: list[dict] = []
            for bucket in resp.get("ResultsByTime", []):
                page.extend(self._flatten(bucket))
            yield page
            token = resp.get("NextPageToken")
            if not token:
                break
