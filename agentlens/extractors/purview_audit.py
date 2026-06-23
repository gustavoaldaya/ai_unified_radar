"""``ext-purview-audit``. Office 365 Management Activity API (content feed).

Two-level pull: list available content blobs for the window, then fetch each
blob (a JSON array of audit events). ``CopilotEventData.AppIdentity`` maps to
the registry ``packageId`` (critical join key).

Important operational facts (grounded in the O365 Management Activity API docs):
- A subscription to ``Audit.General`` must be **started once** before any
  content exists (``ensure_subscription``); first blobs can take up to ~12h.
- Listing pagination is via the **``NextPageUri`` response header**, not an
  ``@odata.nextLink`` body field.
- ``PublisherIdentifier`` (the vendor tenant GUID) earns a dedicated throttling
  quota. ``startTime``/``endTime`` windows must be <=24h, start <=7 days back.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from extractors.base import BaseExtractor, Page
from extractors.core.azure_http import AzureJsonSource
from schemas.purview_audit import RawAuditEvent

_O365_MGMT_SCOPE = "https://manage.office.com/.default"
_CONTENT_TYPE = "Audit.General"


class PurviewAuditExtractor(AzureJsonSource, BaseExtractor):
    name = "ext-purview-audit"
    schema = RawAuditEvent
    source_path = "m365/purview/audit_log"
    source_id_field = "record_id"
    timestamp_field = "creation_date"
    agent_id_field = "app_identity"

    def _base(self) -> str:
        return os.environ.get("O365_MGMT_BASE", "https://manage.office.com")

    def _tenant_publisher(self) -> tuple[str, str]:
        tenant = os.environ["AZURE_TENANT_ID"]
        return tenant, os.environ.get("O365_MGMT_PUBLISHER_ID", tenant)

    def ensure_subscription(self) -> None:  # pragma: no cover - network
        """One-time: start the Audit.General subscription. Idempotent (400 =
        already enabled is tolerated). Run once per tenant before first pull."""
        token = self._aad_token(_O365_MGMT_SCOPE)
        tenant, publisher = self._tenant_publisher()
        url = (
            f"{self._base()}/api/v1.0/{tenant}/activity/feed/subscriptions/start"
            f"?contentType={_CONTENT_TYPE}&PublisherIdentifier={publisher}"
        )
        status, _body, _retry = self._post_json(url, token, {})
        if status not in (200, 400):
            raise RuntimeError(f"start subscription failed: HTTP {status}")

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        token = self._aad_token(_O365_MGMT_SCOPE)
        limiter = self.rate_limit()
        tenant, publisher = self._tenant_publisher()
        url: str | None = (
            f"{self._base()}/api/v1.0/{tenant}/activity/feed/subscriptions/content"
            f"?contentType={_CONTENT_TYPE}&PublisherIdentifier={publisher}"
        )
        while url:
            limiter.before_request()
            status, blobs, headers = self._get_with_headers(url, token)
            if status == 429:
                retry_after = headers.get("Retry-After")
                limiter.on_response(
                    429, retry_after=float(retry_after) if retry_after else None
                )
                continue
            if status != 200:
                raise RuntimeError(f"O365 Mgmt API content list returned HTTP {status}")
            for blob in blobs or []:
                content_uri = blob.get("contentUri")
                if not content_uri:
                    continue
                limiter.before_request()
                bstatus, events, _bheaders = self._get_with_headers(content_uri, token)
                if bstatus == 200 and events:
                    yield list(events)
            url = headers.get("NextPageUri")
