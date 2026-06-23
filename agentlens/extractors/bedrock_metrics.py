"""``ext-bedrock-metrics``. CloudWatch GetMetricData (AWS/Bedrock + AgentCore)."""

from __future__ import annotations

from collections.abc import Iterator

from extractors.base import BaseExtractor, Page
from extractors.core.aws_client import AwsClientSource
from extractors.core.dedup import dedup_key
from schemas.base import RawRecord
from schemas.bedrock_metrics import RawBedrockMetric


class BedrockMetricsExtractor(AwsClientSource, BaseExtractor):
    name = "ext-bedrock-metrics"
    schema = RawBedrockMetric
    source_path = "bedrock/metrics"
    source_id_field = "metric_name"
    timestamp_field = "timestamp"

    def _dedup_key(self, record: RawRecord) -> str:
        parts = [
            str(getattr(record, "metric_name", "")),
            str(getattr(record, "model_id", "")),
        ]
        return dedup_key(":".join(parts), str(getattr(record, "timestamp", "")))

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        client = self._aws_client("cloudwatch")
        queries: list[dict[str, object]] = []  # built from configured metric set
        token: str | None = None
        while True:
            kwargs: dict[str, object] = {"MetricDataQueries": queries}
            if token:
                kwargs["NextToken"] = token
            self.rate_limit().before_request()
            resp = client.get_metric_data(**kwargs)
            yield list(resp.get("MetricDataResults", []))
            token = resp.get("NextToken")
            if not token:
                break
