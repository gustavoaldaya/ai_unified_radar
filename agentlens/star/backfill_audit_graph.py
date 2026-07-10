"""Backfill historico de Purview audit via Graph auditLogQuery (asincrono).

Por que un script aparte del extractor ``ext-purview-audit``:
  * La O365 Management Activity API (el feed del extractor) solo retiene
    blobs 7 dias y en ventanas <=24h. El historico (180 dias en Audit
    Standard) se recupera con la Purview Audit Search API de Graph:
    ``POST /security/auditLog/queries`` (job asincrono en el servicio) +
    ``GET .../queries/{id}/records`` (paginado @odata.nextLink).
  * El enum ``recordTypeFilters`` de Graph v1.0 va por detras de los record
    types Copilot/Agent365 (261/334/363/407). El filtro servidor robusto es
    ``serviceFilter`` (= propiedad Workload del registro): 'Copilot' y
    'Agent365'. El filtro de alcance cliente (``_to_record``) se aplica
    igualmente, identico al extractor.
  * Permiso requerido en la app (application): **AuditLogsQuery.Read.All**
    con admin consent (Graph). Un 401/403 al crear la query = falta esto.

Salida: los MISMOS parquets que el extractor (schema RawAuditEvent, via
``_to_record`` + ``_to_table`` de ext-purview-audit), pero particionados por
FECHA DE EVENTO (no de run):
    m365/purview/audit_log/dt=<event-date>/part-backfill-<service>-<chunk>.parquet
El solape con lo ya cargado es inocuo: ``fact_agent_audit`` dedupe por
``record_id`` (PK) en el upsert del loader.

Estado resumible en ``raw/_watermarks/ext-purview-audit-backfill.json``
(query ids por chunk). Re-ejecutar retoma el polling y la descarga sin
recrear queries; borrar una entrada del JSON fuerza su re-creacion.
Al arrancar se reconcilia con el servidor (adopta queries
agentlens-backfill-* existentes por displayName). No toca el watermark
del extractor incremental.

Uso (desde agentlens/):
    uv run python .\\star\\backfill_audit_graph.py                     # 180 dias
    uv run python .\\star\\backfill_audit_graph.py --from 2026-05-01 --to 2026-07-10
    uv run python .\\star\\backfill_audit_graph.py --chunk-days 10 --poll-interval 30
Despues de terminar:
    uv run python .\\star\\build_star_pg.py       # carga los parquets nuevos
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)


def _load_env(root: str) -> None:
    """Carga agentlens/.env en os.environ (sin pisar lo ya definido).

    config.py delega la carga del .env en "el runner"; este script es su
    propio runner. Sin dependencia de python-dotenv a proposito.
    """
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_env(_ROOT)

from extractors.core.azure_http import GRAPH_SCOPE  # noqa: E402
from extractors.purview_audit import PurviewAuditExtractor, _to_record  # noqa: E402

GRAPH_BASES = (
    "https://graph.microsoft.com/v1.0/security/auditLog/queries",
    "https://graph.microsoft.com/beta/security/auditLog/queries",
)
STATE_PATH = "_watermarks/ext-purview-audit-backfill.json"
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
MAX_ACTIVE_QUERIES = 8  # el servicio limita las busquedas concurrentes


def _chunks(d_from: date, d_to: date, days: int) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    cur = d_from
    while cur <= d_to:
        end = min(cur + timedelta(days=days - 1), d_to)
        out.append((cur, end))
        cur = end + timedelta(days=1)
    return out


class _State:
    """Estado resumible del backfill, persistido via el StorageBackend."""

    def __init__(self, backend) -> None:
        self._backend = backend
        raw = backend.read_text(STATE_PATH)
        self.data: dict = json.loads(raw) if raw else {"queries": {}}

    def get(self, key: str) -> dict | None:
        return self.data["queries"].get(key)

    def put(self, key: str, value: dict) -> None:
        self.data["queries"][key] = value
        self._backend.write_text_atomic(
            STATE_PATH, json.dumps(self.data, indent=1, sort_keys=True)
        )


def _graph(call, *args, attempts: int = 5):
    """Ejecuta una llamada Graph con backoff en 429/5xx."""
    for attempt in range(1, attempts + 1):
        status, body, retry_after = call(*args)
        if status == 429 or status >= 500:
            wait = retry_after or min(60.0, 5.0 * attempt)
            print(f"[backfill-audit] HTTP {status}; reintento en {wait:.0f}s "
                  f"({attempt}/{attempts})", file=sys.stderr)
            time.sleep(wait)
            continue
        return status, body
    return status, body  # ultimo intento, que decida el llamador


def _resolve_base(ext, token: str) -> str:
    """Elige v1.0 o beta sondeando el listado de queries (GET) antes de crear.

    Observado 2026-07-10: POST /v1.0/security/auditLog/queries devuelve 404
    UnknownError en tenants donde beta funciona -- Learn aun referencia la
    Audit Search Graph API en beta y el rollout de v1.0 va por detras.
    Ademas, tras conceder AuditLogsQuery.Read.All el backend de audit puede
    tardar ~15-30 min en reconocer al service principal (403/404
    transitorios con el mismo aspecto).
    """
    last: tuple | None = None
    for base in GRAPH_BASES:
        status, body = _graph(ext._get_json, f"{base}?$top=1", token)
        if status == 200:
            print(f"[backfill-audit] endpoint activo: {base}", file=sys.stderr)
            return base
        last = (base, status, json.dumps(body)[:300])
        print(f"[backfill-audit] sonda {base} -> HTTP {status}", file=sys.stderr)
    raise SystemExit(
        "[backfill-audit] ningun endpoint de auditLog/queries responde "
        f"(ultimo: {last}). Si el permiso AuditLogsQuery.Read.All se acaba "
        "de conceder, el backend de audit puede tardar 15-30 min en "
        "propagarlo: reintentar mas tarde sin tocar nada."
    )


def _adopt_existing(ext, token: str, base: str, state: _State,
                    services: list[str]) -> None:
    """Reconcilia con el servidor antes de crear nada.

    Adopta al estado las queries ``agentlens-backfill-*`` que ya existan
    (runs anteriores o cancelados) para no recrearlas -- los jobs siguen
    corriendo en el servidor aunque el cliente muera -- e imprime el
    inventario de jobs: la cuota de busquedas concurrentes por principal
    es la causa tipica de 429 persistentes en la creacion.
    """
    canonical = {s.lower(): s for s in services}
    url: str | None = f"{base}?$top=100"
    adopted = total = non_terminal = 0
    while url:
        status, body = _graph(ext._get_json, url, token)
        if status != 200:
            print(f"[backfill-audit] WARN no se pudo listar queries "
                  f"(HTTP {status}); sigo sin reconciliar", file=sys.stderr)
            return
        for q in body.get("value") or []:
            total += 1
            if str(q.get("status")) not in TERMINAL:
                non_terminal += 1
            name = str(q.get("displayName") or "")
            if not name.startswith("agentlens-backfill-"):
                continue
            rest = name[len("agentlens-backfill-"):]
            day, svc_lower = rest[-10:], rest[:-11]
            svc = canonical.get(svc_lower)
            if svc is None:
                continue
            key = f"{svc}|{day}"
            if state.get(key) is None:
                state.put(key, {"id": str(q.get("id")),
                                "status": str(q.get("status")),
                                "done": False, "adopted": True})
                adopted += 1
                print(f"[backfill-audit] adoptada {key} (ya existia en el "
                      f"servidor, status={q.get('status')})", file=sys.stderr)
        url = body.get("@odata.nextLink")
    print(f"[backfill-audit] inventario servidor: {total} queries de audit "
          f"({non_terminal} no terminales); {adopted} adoptadas al estado",
          file=sys.stderr)


def _create_query(ext, token: str, base: str, service: str,
                  start: date, end: date) -> str | None:
    payload = {
        "@odata.type": "#microsoft.graph.security.auditLogQuery",
        "displayName": f"agentlens-backfill-{service.lower()}-{start.isoformat()}",
        "filterStartDateTime": f"{start.isoformat()}T00:00:00Z",
        # fin exclusivo: medianoche del dia siguiente al ultimo dia del chunk
        "filterEndDateTime": f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z",
        "serviceFilter": service,
    }
    status, body = _graph(ext._post_json, base, token, payload)
    if status in (200, 201):
        return str(body["id"])
    detail = json.dumps(body)[:400]
    if status == 429:
        # throttling agotado: no tirar el run; el chunk queda sin crear y el
        # siguiente barrido lo reintenta (state.get(key) sigue siendo None).
        print(f"[backfill-audit] 429 persistente creando {service}|{start}; "
              "se reintenta en el proximo barrido", file=sys.stderr)
        return None
    if status in (401, 403):
        raise SystemExit(
            f"[backfill-audit] HTTP {status} creando la query: la app necesita "
            "el permiso APPLICATION 'AuditLogsQuery.Read.All' (Microsoft Graph) "
            f"con admin consent. Detalle: {detail}"
        )
    raise RuntimeError(f"crear query fallo: HTTP {status}: {detail}")


def _poll_status(ext, token: str, base: str, query_id: str) -> str:
    status, body = _graph(ext._get_json, f"{base}/{query_id}", token)
    if status != 200:
        raise RuntimeError(f"poll de {query_id} fallo: HTTP {status}")
    return str(body.get("status") or "unknown")


def _download(ext, token: str, base: str, query_id: str) -> list[dict]:
    """Pagina los records del job y los mapea con el _to_record del extractor.

    Graph envuelve el evento original (PascalCase, mismo shape que los blobs
    de la Management Activity API) en ``auditData``; el sobre aporta
    fallbacks (id, createdDateTime, service, userPrincipalName).
    """
    mapped: list[dict] = []
    url: str | None = f"{base}/{query_id}/records?$top=999"
    page = 0
    while url:
        status, body = _graph(ext._get_json, url, token)
        if status != 200:
            raise RuntimeError(f"descarga de {query_id} fallo: HTTP {status}")
        page += 1
        for rec in body.get("value") or []:
            data = rec.get("auditData")
            if not isinstance(data, dict):
                data = {}
            data.setdefault("Id", rec.get("id"))
            data.setdefault("CreationTime", rec.get("createdDateTime"))
            data.setdefault("Operation", rec.get("operation"))
            data.setdefault("UserId",
                            rec.get("userPrincipalName") or rec.get("userId"))
            data.setdefault("Workload", rec.get("service"))
            record = _to_record(data)
            if record is not None:
                mapped.append(record)
        if page % 10 == 0:
            print(f"[backfill-audit]   ... {page} paginas, "
                  f"{len(mapped)} eventos relevantes", file=sys.stderr)
        url = body.get("@odata.nextLink")
    return mapped


def _write_chunk(ext, raw_records: list[dict], service: str,
                 chunk_start: str) -> tuple[int, list[str]]:
    """Valida/dedup con la maquinaria del extractor y escribe parquet por
    FECHA DE EVENTO. Nombre de fichero estable por (dia, servicio, chunk):
    re-ejecutar un chunk sobreescribe sus propios ficheros (idempotente)."""
    valid, invalid = ext.validate(raw_records)
    if invalid:
        ext.quarantine.write(invalid, datetime.now(timezone.utc).date())
        print(f"[backfill-audit]   {len(invalid)} registros a cuarentena",
              file=sys.stderr)
    deduped = ext.dedup(valid)
    by_day: dict[str, list] = defaultdict(list)
    for record in deduped:
        day = str(getattr(record, "creation_date", "") or "")[:10]
        by_day[day or "unknown"].append(record)
    written: list[str] = []
    for day, rows in sorted(by_day.items()):
        rel = (f"{ext.source_path}/dt={day}/"
               f"part-backfill-{service.lower()}-{chunk_start}.parquet")
        ext.backend.write_parquet(rel, ext._to_table(rows))
        written.append(rel)
    return len(deduped), written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill de Purview audit (Graph auditLogQuery) al raw zone")
    parser.add_argument("--from", dest="date_from",
                        help="inicio YYYY-MM-DD (default: hoy-180d)")
    parser.add_argument("--to", dest="date_to",
                        help="fin YYYY-MM-DD inclusive (default: hoy)")
    parser.add_argument("--chunk-days", type=int, default=15,
                        help="dias por query asincrona (default 15)")
    parser.add_argument("--services", default="Copilot,Agent365",
                        help="serviceFilter por workload (default Copilot,Agent365)")
    parser.add_argument("--poll-interval", type=float, default=60.0,
                        help="segundos entre barridos de polling (default 60)")
    parser.add_argument("--max-wait-minutes", type=float, default=240.0,
                        help="corta el run (resumible) tras N minutos (default 240)")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    d_to = date.fromisoformat(args.date_to) if args.date_to else today
    d_from = (date.fromisoformat(args.date_from) if args.date_from
              else today - timedelta(days=180))
    services = [s.strip() for s in args.services.split(",") if s.strip()]
    chunks = _chunks(d_from, d_to, args.chunk_days)

    ext = PurviewAuditExtractor()
    state = _State(ext.backend)
    keys = [(svc, cs, ce) for svc in services for cs, ce in chunks]
    print(f"[backfill-audit] rango {d_from}..{d_to} | {len(chunks)} chunks x "
          f"{len(services)} servicios = {len(keys)} queries", file=sys.stderr)

    token = ext._aad_token(GRAPH_SCOPE)
    base = _resolve_base(ext, token)
    _adopt_existing(ext, token, base, state, services)

    deadline = time.monotonic() + args.max_wait_minutes * 60.0
    total_events = 0
    failed: list[str] = []
    while True:
        token = ext._aad_token(GRAPH_SCOPE)
        active = sum(
            1 for _svc, cs, _ce in keys
            if (entry := state.get(f"{_svc}|{cs.isoformat()}")) is not None
            and entry.get("id") and entry.get("status") not in TERMINAL
        )
        # crear queries que falten: como mucho UNA por barrido (el servicio
        # limita el ritmo de creacion; los jobs ya creados corren en paralelo)
        for svc, cs, ce in keys:
            key = f"{svc}|{cs.isoformat()}"
            if state.get(key) is not None or active >= MAX_ACTIVE_QUERIES:
                continue
            qid = _create_query(ext, token, base, svc, cs, ce)
            if qid is None:
                break  # 429 agotado: no insistir con mas creaciones este barrido
            state.put(key, {"id": qid, "status": "notStarted", "done": False,
                            "range": [cs.isoformat(), ce.isoformat()]})
            active += 1
            print(f"[backfill-audit] creada {key} -> {qid}", file=sys.stderr)
            break  # una creacion por barrido

        pending = [(svc, cs) for svc, cs, _ce in keys
                   if (e := state.get(f"{svc}|{cs.isoformat()}")) is None
                   or not e.get("done")]
        if not pending:
            break

        for svc, cs in pending:
            key = f"{svc}|{cs.isoformat()}"
            entry = state.get(key)
            if entry is None:  # aun sin crear (tope de concurrencia)
                continue
            status = _poll_status(ext, token, base, entry["id"])
            if status != entry.get("status"):
                entry = {**entry, "status": status}
                state.put(key, entry)
                print(f"[backfill-audit] {key}: {status}", file=sys.stderr)
            if status == "succeeded":
                records = _download(ext, token, base, entry["id"])
                count, files = _write_chunk(ext, records, svc, cs.isoformat())
                total_events += count
                state.put(key, {**entry, "done": True, "records": count,
                                "files": files})
                print(f"[backfill-audit] {key}: {count} eventos relevantes -> "
                      f"{len(files)} parquets", file=sys.stderr)
            elif status in ("failed", "cancelled"):
                state.put(key, {**entry, "done": True, "error": True})
                failed.append(key)
                print(f"[backfill-audit] WARN {key}: {status} (borrar su "
                      "entrada del state JSON para recrearla)", file=sys.stderr)

        pending = [(svc, cs) for svc, cs, _ce in keys
                   if not (state.get(f"{svc}|{cs.isoformat()}") or {}).get("done")]
        if not pending:
            break
        if time.monotonic() > deadline:
            print("[backfill-audit] max-wait alcanzado; estado persistido -- "
                  "re-ejecutar el script para retomar", file=sys.stderr)
            return 2
        time.sleep(args.poll_interval)

    print(f"[backfill-audit] COMPLETADO: {total_events} eventos relevantes "
          f"escritos al raw zone" + (f"; {len(failed)} chunks fallidos: "
          f"{failed}" if failed else ""), file=sys.stderr)
    print("[backfill-audit] siguiente paso: "
          "uv run python .\\star\\build_star_pg.py", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
