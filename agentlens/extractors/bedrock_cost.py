"""``ext-bedrock-cost``. Cost Explorer GetCostAndUsage (model/service level)."""

from __future__ import annotations

from collections.abc import Iterator

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

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        client = self._aws_client("ce")
        token: str | None = None
        while True:
            kwargs: dict[str, object] = {
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
            yield list(resp.get("ResultsByTime", []))
            token = resp.get("NextPageToken")
            if not token:
                break
