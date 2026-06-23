"""Schema for ext-purview-audit. Office 365 Management Activity API."""

from __future__ import annotations

from pydantic import Field

from schemas.base import RawRecord


class RawAuditEvent(RawRecord):
    record_id: str  # audit "Id"
    creation_date: str | None = None
    record_type: str | None = None  # CopilotInteraction / AIApp / Agent365Activities
    operation: str | None = None
    app_identity: str | None = None  # maps to Agent Registry packageId (join key)
    user_id: str | None = None
    workload: str | None = None
    sensitivity_labels: list[str] = Field(default_factory=list)
    dlp_matches: list[str] = Field(default_factory=list)
