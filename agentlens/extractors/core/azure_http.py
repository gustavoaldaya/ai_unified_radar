"""HTTP seams for Azure/Graph-backed extractors.

A mixin (not a ``BaseExtractor``) so extractors can multiple-inherit:
``class FoundryCostExtractor(AzureJsonSource, BaseExtractor): ...``. The
``_aad_token`` / ``_get_json`` / ``_post_json`` methods are the network seams;
tests override them so pagination logic runs without credentials or network.
Azure SDK / urllib are imported lazily, so fixtures runs need neither.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
ARM_SCOPE = "https://management.azure.com/.default"
LOG_ANALYTICS_SCOPE = "https://api.loganalytics.io/.default"

JsonResponse = tuple[int, dict[str, Any], float | None]


class AzureJsonSource:
    """Token acquisition + JSON GET/POST over HTTPS."""

    def _aad_token(self, scope: str) -> str:
        from azure.identity import DefaultAzureCredential

        return str(DefaultAzureCredential().get_token(scope).token)

    def _get_json(
        self, url: str, token: str
    ) -> JsonResponse:  # pragma: no cover - network
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"}
        )
        return self._send(request)

    def _post_json(
        self, url: str, token: str, payload: dict[str, Any]
    ) -> JsonResponse:  # pragma: no cover - network
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return self._send(request)

    @staticmethod
    def _send(
        request: urllib.request.Request,
    ) -> JsonResponse:  # pragma: no cover - network
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
                return response.status, body, None
        except urllib.error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                error_body = json.loads(raw)
            except ValueError:
                error_body = {"_raw": raw}
            return exc.code, error_body, float(retry_after) if retry_after else None

    def _get_with_headers(
        self, url: str, token: str
    ) -> tuple[int, Any, dict[str, str]]:  # pragma: no cover - network
        """GET returning (status, json_body, response_headers).

        Needed by sources that paginate via a response header (e.g. the O365
        Management Activity API's ``NextPageUri``) rather than a body field.
        """
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
                return response.status, body, dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, {}, dict(exc.headers or {})
