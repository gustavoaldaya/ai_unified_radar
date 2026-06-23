"""Shared test fixtures and the reference SampleExtractor used across tests."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from enum import Enum
from typing import Any

import pytest

# Make the repo root importable when running from the agentlens/ subproject.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from extractors.base import BaseExtractor, Page  # noqa: E402
from schemas.base import RawRecord  # noqa: E402

FIXTURES_ROOT = os.path.join(os.path.dirname(__file__), "fixtures")


class AgentType(str, Enum):
    copilot_studio = "copilot_studio"
    agent_builder = "agent_builder"
    custom_engine = "custom_engine"
    sharepoint = "sharepoint"


class SampleAgentRecord(RawRecord):
    package_id: str
    agent_type: AgentType
    last_modified: str
    enabled: bool
    name: str | None = None
    interaction_count: int | None = None


class SampleExtractor(BaseExtractor):
    name = "sample_agents"
    schema = SampleAgentRecord
    source_path = "sample/agents"
    source_id_field = "package_id"
    timestamp_field = "last_modified"
    agent_id_field = "package_id"

    def paginate(self, *, since: str | None) -> Iterator[Page]:  # pragma: no cover
        raise AssertionError("paginate() must not be called under USE_FIXTURES")


@pytest.fixture
def memory_spans() -> Iterator[list[Any]]:
    """Capture OTel spans in-memory for telemetry assertions."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter.get_finished_spans  # type: ignore[misc]
