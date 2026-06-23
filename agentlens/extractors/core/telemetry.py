"""OpenTelemetry helper for extractor spans.

Emits ``gen_ai.*`` semantic-convention attributes plus ``agent.id`` so cost and
usage can be attributed per agent (aligned to OBS-01 v0.5.0). If the OTel API is
unavailable the context manager is a no-op and yields ``None``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

try:
    from opentelemetry import trace

    _TRACER = trace.get_tracer("agentlens.extractors")
    _OTEL_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when OTel is absent
    _OTEL_AVAILABLE = False


@contextmanager
def extractor_span(
    name: str,
    *,
    agent_id: str | None = None,
    system: str = "agentlens",
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Open a span named ``extractor.{name}`` with gen_ai.* attributes."""
    if not _OTEL_AVAILABLE:
        yield None
        return
    with _TRACER.start_as_current_span(f"extractor.{name}") as span:
        span.set_attribute("gen_ai.system", system)
        span.set_attribute("gen_ai.operation.name", "extract")
        if agent_id is not None:
            span.set_attribute("agent.id", agent_id)
        for key, value in (attributes or {}).items():
            span.set_attribute(key, value)
        yield span
