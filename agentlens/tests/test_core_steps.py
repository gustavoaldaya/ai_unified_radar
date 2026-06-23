"""Per-step unit tests (AC-1.3-1) plus AC-1.3-2, AC-1.3-3, AC-1.3-4."""

from __future__ import annotations

from datetime import date

import pyarrow.parquet as pq
import pytest

from extractors.core.calendar import last_business_day
from extractors.core.dedup import dedup, dedup_key
from extractors.core.quarantine import QuarantinedRecord
from extractors.core.ratelimit import HardRateLimitExceeded, RateLimiter
from extractors.core.storage import LocalStorageBackend
from extractors.core.watermark import Watermark, WatermarkStore


# --- calendar (AC-1.3-8 LBD logic) ---------------------------------------- #
def test_lbd_weekday_is_yesterday() -> None:
    assert last_business_day(date(2026, 6, 18)) == date(2026, 6, 17)  # Thu -> Wed


def test_lbd_monday_is_previous_friday() -> None:
    assert last_business_day(date(2026, 6, 15)) == date(2026, 6, 12)  # Mon -> Fri


def test_lbd_sunday_is_previous_friday() -> None:
    assert last_business_day(date(2026, 6, 14)) == date(2026, 6, 12)


def test_lbd_skips_holiday() -> None:
    holidays = frozenset({date(2026, 6, 17)})
    assert last_business_day(date(2026, 6, 18), holidays=holidays) == date(2026, 6, 16)


# --- dedup (AC-1.3-3) ----------------------------------------------------- #
def test_dedup_key_is_deterministic() -> None:
    assert dedup_key("a", "t") == dedup_key("a", "t")
    assert dedup_key("a", "t") != dedup_key("b", "t")


def test_dedup_preserves_first_and_drops_repeats() -> None:
    items = [("a", 1), ("b", 2), ("a", 3)]
    out = dedup(items, key_fn=lambda x: x[0])
    assert out == [("a", 1), ("b", 2)]


# --- rate limiting (AC-1.3-4) --------------------------------------------- #
def test_429_honors_retry_after_no_immediate_retry() -> None:
    slept: list[float] = []
    limiter = RateLimiter(sleep=slept.append, clock=lambda: 0.0)
    waited = limiter.on_response(429, retry_after=2.0)
    assert waited == 2.0
    assert slept == [2.0]  # slept exactly Retry-After; no second request issued here


def test_429_exponential_backoff_without_retry_after() -> None:
    slept: list[float] = []
    limiter = RateLimiter(base_backoff_s=1.0, sleep=slept.append, clock=lambda: 0.0)
    limiter.on_response(429)
    limiter.on_response(429)
    assert slept == [1.0, 2.0]


def test_hard_limit_raises_instead_of_retrying() -> None:
    limiter = RateLimiter(no_retry_on_hard_limit=True, sleep=lambda _: None)
    with pytest.raises(HardRateLimitExceeded):
        limiter.on_response(429)


def test_hourly_quota_blocks_with_fake_clock() -> None:
    now = [0.0]
    slept: list[float] = []
    limiter = RateLimiter(max_per_hour=2, sleep=slept.append, clock=lambda: now[0])
    limiter.before_request()
    limiter.before_request()
    limiter.before_request()  # third within the hour -> must wait
    assert slept and slept[0] > 0.0


# --- watermark (AC-1.3-2) ------------------------------------------------- #
def test_watermark_roundtrip_and_empty(tmp_path) -> None:
    store = WatermarkStore(LocalStorageBackend(str(tmp_path)))
    assert store.read("ext-x") == Watermark.empty("ext-x")
    store.persist(
        Watermark("ext-x", last_success="2026-06-18T00:00:00", cursor="2026-06-17")
    )
    reloaded = store.read("ext-x")
    assert reloaded.cursor == "2026-06-17"
    assert reloaded.last_success == "2026-06-18T00:00:00"


def test_watermark_write_is_atomic_no_tmp_left(tmp_path) -> None:
    store = WatermarkStore(LocalStorageBackend(str(tmp_path)))
    store.persist(Watermark("ext-x", cursor="2026-06-17"))
    leftovers = list(tmp_path.rglob("*.tmp")) + [p for p in tmp_path.rglob("tmp*")]
    assert leftovers == []


# --- storage (AC-1.3-6 typed parquet) ------------------------------------- #
def test_local_text_roundtrip(tmp_path) -> None:
    backend = LocalStorageBackend(str(tmp_path))
    assert backend.read_text("missing.txt") is None
    backend.write_text_atomic("a/b.txt", "hello")
    assert backend.read_text("a/b.txt") == "hello"


def test_local_parquet_typed(tmp_path) -> None:
    import pyarrow as pa

    backend = LocalStorageBackend(str(tmp_path))
    table = pa.table({"package_id": ["x"], "enabled": [True], "n": [1]})
    path = backend.write_parquet("sample/dt=2026-06-17/part-0.parquet", table)
    read = pq.read_table(path)
    assert read.column("enabled").type == pa.bool_()
    assert read.num_rows == 1


# --- quarantine (AC-1.3-5) ------------------------------------------------ #
def test_quarantine_empty_returns_none(tmp_path) -> None:
    from extractors.core.quarantine import Quarantine

    q = Quarantine(LocalStorageBackend(str(tmp_path)), "ext-x")
    assert q.write([], date(2026, 6, 17)) is None


def test_quarantine_writes_jsonl(tmp_path) -> None:
    from extractors.core.quarantine import Quarantine

    backend = LocalStorageBackend(str(tmp_path))
    q = Quarantine(backend, "ext-x")
    rel = q.write([QuarantinedRecord(raw={"bad": 1}, error="boom")], date(2026, 6, 17))
    assert rel is not None
    assert backend.read_text(rel) is not None
    assert "boom" in (backend.read_text(rel) or "")
