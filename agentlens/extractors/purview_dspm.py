"""``ext-purview-dspm``. Purview DSPM AI posture via Graph Security API."""

from __future__ import annotations

import os
from collections.abc import Iterator

from extractors.base import BaseExtractor, Page
from extractors.core.azure_http import GRAPH_SCOPE, AzureJsonSource
from schemas.purview_dspm import RawDspmPosture

_DEFAULT_URL = (
    "https://graph.microsoft.com/beta/security/dataSecurityAndGovernance/aiInteractions"
)


class PurviewDspmExtractor(AzureJsonSource, BaseExtractor):
    name = "ext-purview-dspm"
    schema = RawDspmPosture
    source_path = "m365/purview/dspm_posture"
    source_id_field = "dspm_agent_instance_id"
    timestamp_field = "assessment_date"
    agent_id_field = "entra_agent_id"

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        token = self._aad_token(GRAPH_SCOPE)
        limiter = self.rate_limit()
        url: str | None = os.environ.get("GRAPH_DSPM_URL", _DEFAULT_URL)
        while url:
            limiter.before_request()
            status, body, retry_after = self._get_json(url, token)
            if status == 429:
                limiter.on_response(429, retry_after=retry_after)
                continue
            if status != 200:
                raise RuntimeError(f"Graph Security returned HTTP {status}")
            yield list(body.get("value", []))
            url = body.get("@odata.nextLink")
