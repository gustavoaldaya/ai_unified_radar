"""Runner LOCAL de extractores: identico a extractors.run pero fuerza el backend
LOCAL (LocalStorageBackend -> _local_raw) en vez de build_backend, que en modo
live escribe a ADLS. Fase actual: la data se concentra en Postgres via
build_star_pg.py (que lee _local_raw); la migracion a storage/ADLS es posterior.

Uso (desde agentlens/):
    uv run python star/run_local.py --all
    uv run python star/run_local.py ext-bedrock-traces ext-bedrock-metrics
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from extractors.run import _load_dotenv
_load_dotenv()

from extractors.catalog import CATALOG, get_extractor
from extractors.core.config import Settings
from extractors.core.storage import LocalStorageBackend


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_local")
    parser.add_argument("names", nargs="*", help="extractor names to run")
    parser.add_argument("--all", action="store_true", help="run every catalog extractor")
    args = parser.parse_args(argv)

    names = sorted(CATALOG) if args.all else args.names
    if not names:
        parser.error("pass extractor names or --all")

    settings = Settings.from_env()
    backend = LocalStorageBackend(settings.raw_root)  # fuerza local, no ADLS
    exit_code = 0
    for name in names:
        ext = get_extractor(name)(settings=settings, backend=backend)
        try:
            r = ext.run()
            print(f"[ok:local] {name}: {r.record_count} records, {r.duplicate_count} dup, "
                  f"{r.invalid_count} invalid, dt={r.target_date} -> {r.written_path}")
        except Exception as exc:  # noqa: BLE001
            exit_code = 1
            print(f"[fail:local] {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())