"""Schema for ext-purview-dspm. Purview REST / Graph Security API."""

from __future__ import annotations

from pydantic import Field

from schemas.base import RawRecord


class RawDspmPosture(RawRecord):
    dspm_agent_instance_id: str
    assessment_date: str | None = None
    insider_risk_level: str | None = None  # Low / Medium / High
    sensitivity_accessed: list[str] = Field(default_factory=list)
    dlp_violations: int | None = None
    oversharing_detected: bool | None = None
    entra_agent_id: str | None = None
    package_id: str | None = None  # cross-ref to registry
