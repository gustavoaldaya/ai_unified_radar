"""``ext-m365-user-activity``. Graph Reports general M365 usage (per-user)."""

from __future__ import annotations

import os
from collections.abc import Iterator

from extractors.base import BaseExtractor, Page
from extractors.core.azure_http import GRAPH_SCOPE, AzureJsonSource
from schemas.m365_user_activity import RawUserActivity

_DEFAULT_URL = (
    "https://graph.microsoft.com/v1.0/reports/getTeamsUserActivityUserDetail(period='D7')"
    "?$format=application/json"
)


class M365UserActivityExtractor(AzureJsonSource, BaseExtractor):
    name = "ext-m365-user-activity"
    schema = RawUserActivity
    source_path = "m365-user-activity"
    source_id_field = "user_id"
    timestamp_field = "report_date"

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        token = self._aad_token(GRAPH_SCOPE)
        limiter = self.rate_limit()
        url: str | None = os.environ.get("GRAPH_USER_ACTIVITY_URL", _DEFAULT_URL)
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
