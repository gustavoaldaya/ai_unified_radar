"""Consolida a parquet los parciales de backfill YA descargados, sin esperar a
que termine la paginacion de cada chunk.

Por que existe: ``backfill_audit_graph.py`` solo escribe parquet cuando un chunk
se drena por completo (``next_url is None``). Si la ``resume_url`` de Graph ha
caducado (los jobs de audit no se retienen indefinidamente), esos chunks nunca
alcanzan la completitud y los ~cientos de miles de eventos ya bajados a
``_watermarks/backfill-partials/*.ndjson`` se quedan sin cargar. Este helper los
materializa a parquet REUTILIZANDO la maquinaria del extractor
(``ext.validate`` + ``ext.dedup`` + ``ext._to_table`` via ``_write_chunk``), de
modo que el parquet lleva la columna ``_drift`` con el ``CopilotEventData`` que
``build_star_pg.load_audit`` necesita para resolver ``native_agent_id``,
``AppHost``, etiquetas de sensibilidad, etc. Sin pasar por ``_to_table`` el
parquet no tendria ``_drift`` y todo caeria al centinela sin atribucion.

Es puramente local: NO llama a Graph ni necesita token. Idempotente: reescribe
el mismo parquet por (dia, servicio, chunk); el loader deduplica por
``record_id`` en el upsert. NO muta el estado: los chunks siguen ``done:false``,
asi que un run posterior del backfill (o su recreacion) puede completarlos y
re-materializar el parquet con los datos integros.

Uso (desde agentlens/):
    uv run python .\\star\\flush_backfill_partials.py
Despues:
    uv run python .\\star\\build_star_pg.py            # incremental
    # (cuando mas adelante completes el backfill entero, recarga esos
    #  parquets enriquecidos con:  build_star_pg.py --full-scan)
"""

from __future__ import annotations

import sys

# backfill_audit_graph vive en este mismo directorio (star/), que Python pone en
# sys.path[0] al ejecutar el script; el propio modulo inserta la raiz agentlens/
# para poder importar el paquete extractors.
from backfill_audit_graph import (  # noqa: E402
    PurviewAuditExtractor,
    _State,
    _read_partial_parts,
    _write_chunk,
)


def main() -> int:
    ext = PurviewAuditExtractor()
    state = _State(ext.backend)

    total_read = total_events = total_files = 0
    flushed = skipped = 0

    for key, entry in sorted(state.data["queries"].items()):
        parts = int(entry.get("partial_parts") or 0)
        if parts <= 0:
            continue  # sin parciales en disco (fallido, running o sin drenar)
        if entry.get("done"):
            skipped += 1
            continue  # ya consolidado en un run anterior
        svc, _, day = key.partition("|")
        records = _read_partial_parts(ext.backend, key, parts)
        if not records:
            print(f"[flush] {key}: {parts} parts pero 0 registros legibles",
                  file=sys.stderr)
            continue
        count, files = _write_chunk(ext, records, svc, day)
        total_read += len(records)
        total_events += count
        total_files += len(files)
        flushed += 1
        print(f"[flush] {key}: {len(records)} descargados -> {count} tras "
              f"validate/dedup -> {len(files)} parquet(s)", file=sys.stderr)

    print(f"[flush] COMPLETADO: {flushed} chunks materializados "
          f"({skipped} ya estaban done), {total_read} eventos leidos -> "
          f"{total_events} escritos en {total_files} parquets.", file=sys.stderr)
    print("[flush] siguiente paso: uv run python .\\star\\build_star_pg.py",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
