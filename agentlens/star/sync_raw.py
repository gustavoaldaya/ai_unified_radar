"""Sync incremental ADLS raw/ -> _local_raw (capa de landing del star loader).

Elimina el paso manual de ``az storage fs file download`` por fichero (y la
dependencia de AzCopy, que fallo por DNS el 2026-07-08). Usa el SDK
``azure-storage-file-datalake`` que ya es dependencia del proyecto, con
``DefaultAzureCredential`` (mismo auth que los extractores: az login o SP).

Incremental: se salta ficheros que ya existen en local con el mismo tamano.
Un parquet re-escrito en ADLS (mismo path, distinto tamano) se re-descarga.

Uso (desde agentlens/):
    uv run python star/sync_raw.py                    # prefijos por defecto
    uv run python star/sync_raw.py --prefix foundry/cost
    uv run python star/sync_raw.py --dry-run

Requiere en .env / entorno:
    ADLS_ACCOUNT_URL   p.ej. https://hibtribazurestorage001.dfs.core.windows.net
    ADLS_FILESYSTEM    p.ej. raw
"""

from __future__ import annotations

import argparse
import os
import sys

# Los extractores capturan a estos prefijos (source_path de cada uno).
_DEFAULT_PREFIXES = (
    "bedrock",
    "foundry",
    "purview",
    "m365",
)


def main() -> int:
    parser = argparse.ArgumentParser(prog="sync_raw")
    parser.add_argument("--prefix", action="append", default=None,
                        help="prefijo ADLS a sincronizar (repetible); "
                             f"default: {', '.join(_DEFAULT_PREFIXES)}")
    parser.add_argument("--local-root", default="_local_raw",
                        help="raiz local destino (default _local_raw)")
    parser.add_argument("--dry-run", action="store_true",
                        help="lista que se descargaria, sin descargar")
    args = parser.parse_args()

    _load_env_file(".env")
    account_url = os.environ.get("ADLS_ACCOUNT_URL")
    filesystem = os.environ.get("ADLS_FILESYSTEM")
    if not account_url or not filesystem:
        print("ERROR: ADLS_ACCOUNT_URL / ADLS_FILESYSTEM no definidos (.env)",
              file=sys.stderr)
        return 2

    from azure.identity import DefaultAzureCredential
    from azure.storage.filedatalake import DataLakeServiceClient

    service = DataLakeServiceClient(account_url, credential=DefaultAzureCredential())
    fs = service.get_file_system_client(filesystem)

    prefixes = args.prefix or list(_DEFAULT_PREFIXES)
    downloaded = skipped = missing_prefix = 0

    for prefix in prefixes:
        try:
            paths = list(fs.get_paths(path=prefix, recursive=True))
        except Exception as exc:  # prefijo inexistente en ADLS -> informar y seguir
            print(f"[{prefix}] no listable ({type(exc).__name__}): saltado")
            missing_prefix += 1
            continue

        for item in paths:
            if item.is_directory or not item.name.endswith(".parquet"):
                continue
            local_path = os.path.join(args.local_root, *item.name.split("/"))
            remote_size = item.content_length or 0
            if os.path.exists(local_path) and os.path.getsize(local_path) == remote_size:
                skipped += 1
                continue
            print(f"  GET {item.name}  ({remote_size} bytes)")
            downloaded += 1
            if args.dry_run:
                continue
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            tmp = local_path + ".tmp"
            with open(tmp, "wb") as handle:
                fs.get_file_client(item.name).download_file().readinto(handle)
            os.replace(tmp, local_path)

    verb = "por descargar" if args.dry_run else "descargados"
    print(f"\n[sync_raw] {downloaded} {verb}, {skipped} al dia, "
          f"{missing_prefix} prefijos no listables")
    return 0


def _load_env_file(path: str) -> None:
    """Mismo loader minimo que build_star_pg.py (shell gana sobre fichero)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


if __name__ == "__main__":
    raise SystemExit(main())
