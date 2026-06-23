"""CLI runner for AgentLens extractors.

Examples:
    python -m extractors.run --all
    python -m extractors.run ext-m365-registry ext-m365-usage

Honours ``USE_FIXTURES`` (and the rest of ``Settings``) from the environment.
With fixtures it reads ``tests/fixtures/{name}/`` and writes Parquet locally;
the live cutover is the same command with ``USE_FIXTURES=false`` + credentials.
"""

from __future__ import annotations

import argparse
import sys

from extractors.catalog import CATALOG, get_extractor
from extractors.core.config import Settings
from extractors.core.storage import build_backend


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="extractors.run")
    parser.add_argument("names", nargs="*", help="extractor names to run")
    parser.add_argument(
        "--all", action="store_true", help="run every catalog extractor"
    )
    args = parser.parse_args(argv)

    names = sorted(CATALOG) if args.all else args.names
    if not names:
        parser.error("pass extractor names or --all")

    settings = Settings.from_env()
    exit_code = 0
    for name in names:
        extractor = get_extractor(name)(
            settings=settings, backend=build_backend(settings)
        )
        try:
            result = extractor.run()
            print(
                f"[ok] {name}: {result.record_count} records, "
                f"{result.duplicate_count} dup, {result.invalid_count} invalid, "
                f"dt={result.target_date} -> {result.written_path}"
            )
        except Exception as exc:  # noqa: BLE001 - report and continue with the rest
            exit_code = 1
            print(f"[fail] {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
