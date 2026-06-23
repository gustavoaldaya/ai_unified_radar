"""Idempotency helpers.

Dedup key = ``sha256(source_id + ":" + event_timestamp)`` (M1.3 decision).
``dedup`` is order-preserving and keeps the first occurrence of each key.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")


def dedup_key(source_id: str, event_timestamp: str) -> str:
    """Stable content key for a single source event."""
    raw = f"{source_id}:{event_timestamp}".encode()
    return hashlib.sha256(raw).hexdigest()


def dedup(records: Iterable[T], key_fn: Callable[[T], str]) -> list[T]:
    """Drop duplicate records (by ``key_fn``), preserving first-seen order."""
    seen: set[str] = set()
    out: list[T] = []
    for record in records:
        key = key_fn(record)
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out
