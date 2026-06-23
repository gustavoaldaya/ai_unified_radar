"""``ext-m365-usage`` (M1.5).

Daily per-agent usage from Graph Reports ``getCopilotAgentUsage``. Graph Reports
arrive late (T+72h), so the live window is a **3-day moving window** ending at
the target date; dedup by ``package_id + report_date`` makes overlapping windows
idempotent (AC-1.5-2). The tenant may anonymise user data — captured via
``is_anonymized`` (AC-1.5-4). ``reconcile_orphans`` supports AC-1.5-6 (every
usage ``package_id`` should exist in the latest registry snapshot, else logged
as an orphan).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from datetime import date, timedelta

from extractors.base import BaseExtractor, Page
from extractors.core.azure_http import GRAPH_SCOPE, AzureJsonSource
from schemas.m365_usage import RawAgentUsage

_WINDOW_DAYS = 3
_DEFAULT_USAGE_URL = (
    "https://graph.microsoft.com/beta/reports/getCopilotAgentUsage"
    "?$format=application/json"
)


class M365UsageExtractor(AzureJsonSource, BaseExtractor):
    name = "ext-m365-usage"
    schema = RawAgentUsage
    source_path = "m365-usage"  # raw/m365-usage/{date}/ (AC-1.5-5)
    source_id_field = "package_id"
    timestamp_field = "report_date"
    agent_id_field = "package_id"

    @staticmethod
    def window_start(target: date) -> date:
        """First day of the 3-day late-arriving window (LBD-2 .. LBD)."""
        return target - timedelta(days=_WINDOW_DAYS - 1)

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        token = self._aad_token(GRAPH_SCOPE)
        limiter = self.rate_limit()
        url: str | None = os.environ.get("GRAPH_USAGE_URL", _DEFAULT_USAGE_URL)
        while url:
            limiter.before_request()
            status, body, retry_after = self._get_json(url, token)
            if status == 429:
                limiter.on_response(429, retry_after=retry_after)
                continue
            if status != 200:
                raise RuntimeError(f"Graph Reports returned HTTP {status}")
            yield list(body.get("value", []))
            url = body.get("@odata.nextLink")

    @staticmethod
    def reconcile_orphans(
        usage_package_ids: Iterable[str], registry_package_ids: Iterable[str]
    ) -> set[str]:
        """Usage package_ids not present in the latest registry snapshot."""
        return set(usage_package_ids) - set(registry_package_ids)
