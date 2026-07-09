"""``ext-bedrock-metrics``. CloudWatch GetMetricData (AWS/Bedrock + AgentCore)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

from extractors.base import BaseExtractor, Page
from extractors.core.aws_client import AwsClientSource
from extractors.core.dedup import dedup_key
from schemas.base import RawRecord
from schemas.bedrock_metrics import RawBedrockMetric

# GetMetricData caps at 500 queries per call; first-capture stays well under.
_MAX_QUERIES = 500
# 1h buckets over the watermark window.
_PERIOD_SECONDS = 3600


def _stat_for(metric_name: str) -> str:
    """Sum for counts/tokens; Average for latency-like metrics (a Sum of a
    latency is meaningless)."""
    lowered = metric_name.lower()
    if "latency" in lowered or "time" in lowered:
        return "Average"
    return "Sum"


def _iso_timestamp(value: object) -> str:
    """CloudWatch returns timezone-aware datetimes; normalise to isoformat."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


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

    def _window(self, since: str | None) -> tuple[datetime, datetime]:
        """CloudWatch requires an explicit window. Start at the watermark cursor
        (a date isoformat persisted by ``run()``); fall back to a 24h lookback on
        the first run when no cursor exists yet."""
        end = datetime.now(timezone.utc)
        if since:
            start = datetime.fromisoformat(since)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
        else:
            start = end - timedelta(days=1)
        return start, end

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        client = self._aws_client("cloudwatch")
        queries, meta = self._build_queries(client)
        if not queries:
            return
        start, end = self._window(since)
        token: str | None = None
        while True:
            kwargs: dict[str, object] = {
                "MetricDataQueries": queries,
                "StartTime": start,
                "EndTime": end,
            }
            if token:
                kwargs["NextToken"] = token
            self.rate_limit().before_request()
            resp = client.get_metric_data(**kwargs)
            yield list(self._flatten(resp.get("MetricDataResults", []), meta))
            token = resp.get("NextToken")
            if not token:
                break

    def _build_queries(
        self, client: Any
    ) -> tuple[list[dict], dict[str, dict]]:
        """Discover live metrics via ``list_metrics`` (auto-picks up ModelId
        values without a hard-coded model inventory) and build one MetricStat
        query per (metric, dimension-set). ``meta`` maps each query Id back to
        the fields the flattener needs, since GetMetricData results only echo
        the Id and Label."""
        queries: list[dict] = []
        meta: dict[str, dict] = {}
        for namespace in self.settings.bedrock_metric_namespaces:
            for metric in self._list_metrics(client, namespace):
                if len(queries) >= _MAX_QUERIES:
                    return queries, meta
                metric_name = metric.get("MetricName")
                if not metric_name:
                    continue
                dimensions = metric.get("Dimensions", [])
                qid = f"m{len(queries)}"
                queries.append(
                    {
                        "Id": qid,
                        "MetricStat": {
                            "Metric": {
                                "Namespace": namespace,
                                "MetricName": metric_name,
                                "Dimensions": dimensions,
                            },
                            "Period": _PERIOD_SECONDS,
                            "Stat": _stat_for(metric_name),
                        },
                        "ReturnData": True,
                    }
                )
                dim_map = {d.get("Name"): d.get("Value") for d in dimensions}
                meta[qid] = {
                    "metric_name": metric_name,
                    "namespace": namespace,
                    "model_id": dim_map.get("ModelId"),
                    "operation_name": dim_map.get("Operation")
                    or dim_map.get("OperationName"),
                }
        return queries, meta

    def _list_metrics(self, client: Any, namespace: str) -> Iterator[dict]:
        token: str | None = None
        while True:
            kwargs: dict[str, object] = {"Namespace": namespace}
            if token:
                kwargs["NextToken"] = token
            self.rate_limit().before_request()
            resp = client.list_metrics(**kwargs)
            yield from resp.get("Metrics", [])
            token = resp.get("NextToken")
            if not token:
                break

    def _flatten(
        self, results: list[dict], meta: dict[str, dict]
    ) -> Iterator[dict]:
        """Zip each result's parallel Timestamps/Values arrays into one flat
        record per datapoint, restoring metric identity from ``meta``."""
        for result in results:
            info = meta.get(result.get("Id"), {})
            timestamps = result.get("Timestamps", [])
            values = result.get("Values", [])
            for timestamp, value in zip(timestamps, values):
                yield {
                    "metric_name": info.get("metric_name"),
                    "timestamp": _iso_timestamp(timestamp),
                    "value": float(value) if value is not None else None,
                    "model_id": info.get("model_id"),
                    "operation_name": info.get("operation_name"),
                    "namespace": info.get("namespace"),
                }
