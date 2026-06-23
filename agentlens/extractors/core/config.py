"""Runtime settings for the extractor framework.

Read from environment (``.env`` is loaded by the runner, never committed).
Under ``USE_FIXTURES=true`` no real credentials are required -- the framework
reads ``tests/fixtures/`` and writes Parquet to a local raw root. See
``architecture/Credentials and Environment`` and ``architecture/Fixtures Strategy``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date


def _as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_holidays(value: str | None) -> frozenset[date]:
    if not value:
        return frozenset()
    out: set[date] = set()
    for token in value.split(","):
        token = token.strip()
        if token:
            out.add(date.fromisoformat(token))
    return frozenset(out)


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration."""

    use_fixtures: bool = True
    raw_root: str = "_local_raw"
    fixtures_root: str = "tests/fixtures"
    business_week_mon_fri: bool = True
    holidays: frozenset[date] = field(default_factory=frozenset)
    # Live-only (unused while use_fixtures is True).
    adls_account_url: str | None = None
    adls_filesystem: str | None = None
    key_vault_uri: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        source = dict(os.environ if env is None else env)
        business_week = source.get("BUSINESS_WEEK", "Mon-Fri").strip().lower()
        return cls(
            use_fixtures=_as_bool(source.get("USE_FIXTURES"), default=True),
            raw_root=source.get("RAW_ROOT", "_local_raw"),
            fixtures_root=source.get("FIXTURES_ROOT", "tests/fixtures"),
            business_week_mon_fri=(business_week == "mon-fri"),
            holidays=_parse_holidays(source.get("HOLIDAY_CALENDAR")),
            adls_account_url=source.get("ADLS_ACCOUNT_URL"),
            adls_filesystem=source.get("ADLS_FILESYSTEM"),
            key_vault_uri=source.get("KEY_VAULT_URI"),
        )
