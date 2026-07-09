"""``ext-purview-audit``. Office 365 Management Activity API (content feed).

Two-level pull: list available content blobs for the window, then fetch each
blob (a JSON array of audit events). Identity join empirico (2026-07-09):
``CopilotEventData.TargetPlatformAgentId`` trae el packageId ``T_...`` del
registro M365 (join directo a ``dim_agent.native_agent_id``, mismo namespace);
el ``AppIdentity`` documentado no aparece en este tenant.

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
import sys
import urllib.error
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

from extractors.base import BaseExtractor, Page
from extractors.core.azure_http import AzureJsonSource
from schemas.purview_audit import RawAuditEvent

_O365_MGMT_SCOPE = "https://manage.office.com/.default"
_CONTENT_TYPE = "Audit.General"
_FIRST_CAPTURE_HOURS = 6  # primera captura acotada: validar sin tragar 24h de tenant
_FEED_TIMEOUT_S = 180.0  # el content feed puede tardar >60s (visto 2026-07-09)
_MAPPED_ROOT = frozenset({
    "Id", "CreationTime", "RecordType", "Operation", "UserId", "Workload",
    "CopilotEventData",
})


def _content_window(since: str | None) -> tuple[str, str]:
    """startTime/endTime UTC para el listado de contenido.

    Sin ventana explicita la API devuelve las ultimas 24h completas -- en un
    tenant activo son miles de blobs secuenciales y el run parece colgado.
    Restricciones de la API: span <=24h y start <=7 dias atras (se clampa).
    Catch-up de varios dias = varias ejecuciones (el watermark avanza por run).
    """
    now = datetime.now(timezone.utc)
    if since:
        start = datetime.fromisoformat(str(since)[:19])
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        floor = now - timedelta(days=7) + timedelta(minutes=10)
        start = max(start, floor)
    else:
        start = now - timedelta(hours=_FIRST_CAPTURE_HOURS)
    end = min(start + timedelta(hours=24), now)
    fmt = "%Y-%m-%dT%H:%M:%S"
    return start.strftime(fmt), end.strftime(fmt)


def _to_record(event: dict) -> dict | None:
    """Mapea el evento crudo (PascalCase de la API) al schema y filtra alcance.

    Audit.General trae el tenant entero (110k+ eventos/6h observados); solo
    interesan los eventos con ``CopilotEventData`` o workload Copilot/AI. El
    resto se descarta aqui como fuera de alcance -- NO via cuarentena, que es
    para registros relevantes malformados, no un filtro.
    ``CopilotEventData`` completo se conserva como campo extra (drift) para el
    modelado posterior sin perder fidelidad.
    """
    ced = event.get("CopilotEventData")
    workload = str(event.get("Workload") or "")
    if ced is None and workload.lower() not in ("copilot", "aiapps", "ai"):
        return None
    record_id = str(event.get("Id") or "")
    if not record_id:
        return None
    record: dict = {
        "record_id": record_id,
        "creation_date": event.get("CreationTime"),
        "record_type": (
            None if event.get("RecordType") is None
            else str(event.get("RecordType"))
        ),
        "operation": event.get("Operation"),
        "user_id": event.get("UserId"),
        "workload": workload or None,
        "app_identity": (
            (ced.get("AppIdentity") or ced.get("TargetPlatformAgentId"))
            if isinstance(ced, dict) else None
        ),
    }
    if isinstance(ced, dict):
        record["CopilotEventData"] = ced
    # campos raiz no mapeados (p.ej. posible id de agente en InferenceCall):
    # se conservan como drift; PascalCase no colisiona con los declarados.
    record.update({k: v for k, v in event.items() if k not in _MAPPED_ROOT})
    return record


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

    def _get_with_retry(
        self, url: str, token: str, *, attempts: int = 3
    ) -> tuple[int, Any, dict[str, str]]:  # pragma: no cover - network
        """GET con timeout largo y reintentos acotados frente a timeouts de
        lectura del feed. Si agota los intentos, propaga: eso senala problema
        de ruta de red (proxy/inspeccion), no lentitud de la API."""
        for attempt in range(1, attempts + 1):
            try:
                return self._get_with_headers(url, token, timeout=_FEED_TIMEOUT_S)
            except (TimeoutError, urllib.error.URLError) as exc:
                print(f"[{self.name}] timeout/red intento {attempt}/{attempts} "
                      f"(timeout={_FEED_TIMEOUT_S:.0f}s): {exc}", file=sys.stderr)
                if attempt == attempts:
                    raise
        raise RuntimeError("unreachable")

    def paginate(
        self, *, since: str | None
    ) -> Iterator[Page]:  # pragma: no cover - network
        start, end = _content_window(since)
        print(f"[{self.name}] ventana {start}..{end} UTC", file=sys.stderr)
        print(f"[{self.name}] adquiriendo token O365 Mgmt (DefaultAzureCredential)...",
              file=sys.stderr)
        token = self._aad_token(_O365_MGMT_SCOPE)
        print(f"[{self.name}] token OK; listando contenido...", file=sys.stderr)
        limiter = self.rate_limit()
        tenant, publisher = self._tenant_publisher()
        url: str | None = (
            f"{self._base()}/api/v1.0/{tenant}/activity/feed/subscriptions/content"
            f"?contentType={_CONTENT_TYPE}&PublisherIdentifier={publisher}"
            f"&startTime={start}&endTime={end}"
        )
        page_no = 0
        while url:
            limiter.before_request()
            status, blobs, headers = self._get_with_retry(url, token)
            if status == 429:
                retry_after = headers.get("Retry-After")
                waited = limiter.on_response(
                    429, retry_after=float(retry_after) if retry_after else None
                )
                print(f"[{self.name}] 429 del feed; reintento tras {waited:.0f}s",
                      file=sys.stderr)
                continue
            if status != 200:
                raise RuntimeError(f"O365 Mgmt API content list returned HTTP {status}")
            page_no += 1
            fetched = 0
            kept = 0
            for blob in blobs or []:
                content_uri = blob.get("contentUri")
                if not content_uri:
                    continue
                limiter.before_request()
                bstatus, events, _bheaders = self._get_with_retry(content_uri, token)
                if bstatus == 200 and events:
                    fetched += 1
                    mapped = [r for r in map(_to_record, events) if r is not None]
                    if mapped:
                        kept += len(mapped)
                        yield mapped
            print(f"[{self.name}] pagina {page_no}: {len(blobs or [])} blobs "
                  f"listados, {fetched} descargados, {kept} eventos relevantes",
                  file=sys.stderr)
            url = headers.get("NextPageUri")
