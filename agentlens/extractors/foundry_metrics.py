"""``ext-foundry-metrics``. Azure Monitor metrics for CognitiveServices accounts."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from extractors.base import BaseExtractor, Page
from extractors.core.azure_http import ARM_SCOPE, AzureJsonSource
from extractors.core.dedup import dedup_key
from schemas.base import RawRecord
from schemas.foundry_metrics import RawFoundryMetric

# Azure Monitor CognitiveServices dimension name (lower-cased) -> schema field.
_DIM_MAP = {
    "modeldeploymentname": "model_deployment_name",
    "modelname": "model_name",
    "modelversion": "model_version",
    "region": "region",
    "apiname": "api_name",
    "statuscode": "status_code",
}
# Preference order when collapsing an Azure datapoint to a single value.
_AGG_KEYS = ("total", "average", "count", "maximum", "minimum")


def _pick_value(datapoint: dict) -> float | None:
    for key in _AGG_KEYS:
        raw = datapoint.get(key)
        if raw is not None:
            return float(raw)
    return None


# Azure Monitor accepts at most 20 metric names per /metrics call.
_MAX_METRICS_PER_CALL = 20
# Dimension we split the timeseries on, to attribute each datapoint to a model
# deployment. Only applied to metrics whose definition exposes it.
_SPLIT_DIMENSION = "ModelDeploymentName"


def _chunked(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _iso_z(moment: datetime) -> str:
    """Azure Monitor's timespan wants a trailing Z, not a +00:00 offset."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _azure_error(body: dict) -> str:
    """Concise message from an Azure REST error body (surfaced in exceptions)."""
    err = body.get("error", body) if isinstance(body, dict) else {}
    if not isinstance(err, dict):
        return str(body)
    code = str(err.get("code", "")).strip()
    message = str(err.get("message") or err.get("_raw") or "").strip()
    return " ".join(p for p in (code, message) if p) or str(body)


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
        base = (
            f"https://management.azure.com{resource_id}"
            "/providers/microsoft.insights"
        )
        splittable, plain = self._metric_names(base, token)
        if not splittable and not plain:
            return
        timespan = self._timespan(since)
        # Metrics exposing ModelDeploymentName: split the series per deployment so
        # each datapoint carries model_deployment_name. The rest stay aggregate.
        yield from self._query(
            base, token, splittable, timespan, f"{_SPLIT_DIMENSION} eq '*'"
        )
        yield from self._query(base, token, plain, timespan, None)

    def _query(
        self,
        base: str,
        token: str,
        names: list[str],
        timespan: str,
        dim_filter: str | None,
    ) -> Iterator[Page]:  # pragma: no cover - network
        for chunk in _chunked(names, _MAX_METRICS_PER_CALL):
            params = urlencode(
                {
                    "api-version": "2023-10-01",
                    "metricnames": ",".join(chunk),
                    "timespan": timespan,
                    "interval": "PT1H",
                }
            )
            url = f"{base}/metrics?{params}"
            if dim_filter:
                url += "&$filter=" + quote(dim_filter, safe="")
            self.rate_limit().before_request()
            status, body, _ = self._get_json(url, token)
            if status != 200:
                raise RuntimeError(
                    f"Azure Monitor /metrics returned HTTP {status}: "
                    f"{_azure_error(body)}"
                )
            yield list(self._flatten(body.get("value", [])))

    def _metric_names(self, base: str, token: str) -> tuple[list[str], list[str]]:
        """Return (splittable, plain) metric names. The bare /metrics call
        returns only the first metric and the last hour -- which is why the first
        live run captured nothing -- so we enumerate from metricDefinitions.
        'splittable' names expose the ModelDeploymentName dimension and can be
        broken down per deployment via $filter; 'plain' names cannot."""
        params = urlencode({"api-version": "2021-05-01"})
        self.rate_limit().before_request()
        status, body, _ = self._get_json(
            f"{base}/metricDefinitions?{params}", token
        )
        if status != 200:
            raise RuntimeError(
                f"Azure Monitor metricDefinitions returned HTTP {status}: "
                f"{_azure_error(body)}"
            )
        splittable: list[str] = []
        plain: list[str] = []
        for definition in body.get("value", []):
            name = (definition.get("name") or {}).get("value")
            if not name:
                continue
            dimensions = {
                str((dim or {}).get("value", "")).lower()
                for dim in definition.get("dimensions", [])
            }
            target = (
                splittable
                if _SPLIT_DIMENSION.lower() in dimensions
                else plain
            )
            target.append(name)
        return splittable, plain

    def _timespan(self, since: str | None) -> str:
        """ISO 8601 window from the watermark cursor (a date isoformat) to now;
        24h lookback on the first run when no cursor exists yet."""
        end = datetime.now(timezone.utc)
        if since:
            start = datetime.fromisoformat(since)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
        else:
            start = end - timedelta(days=1)
        return f"{_iso_z(start)}/{_iso_z(end)}"

    def _flatten(self, metrics: list[dict]) -> Iterator[dict]:
        """Explode the Azure Monitor envelope value[] -> timeseries[] -> data[]
        into one flat record per datapoint. Datapoints with no aggregation value
        (Azure emits timeStamp-only gaps) are skipped."""
        for metric in metrics:
            name = (metric.get("name") or {}).get("value")
            if not name:
                continue
            for series in metric.get("timeseries", []):
                dims = self._dimensions(series.get("metadatavalues", []))
                for datapoint in series.get("data", []):
                    value = _pick_value(datapoint)
                    if value is None:
                        continue
                    yield {
                        "metric_name": name,
                        "timestamp": datapoint.get("timeStamp"),
                        "value": value,
                        **dims,
                    }

    @staticmethod
    def _dimensions(metadatavalues: list[dict]) -> dict[str, str]:
        out: dict[str, str] = {}
        for item in metadatavalues:
            raw_name = ((item.get("name") or {}).get("value") or "").lower()
            field = _DIM_MAP.get(raw_name)
            if field:
                out[field] = item.get("value")
        return out
