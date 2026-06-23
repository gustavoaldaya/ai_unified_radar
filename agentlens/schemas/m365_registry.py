"""Schema for the M365 Agent Registry extractor (M1.4).

``packageId`` is the **canonical agent key** (ADR-003) and the join key to
Purview AppIdentity (ADR-005); it is a required field, which structurally
enforces the AC-1.4-6 quality gate (no null ``package_id`` ever reaches the
raw zone -- a null lands in ``_quarantine/`` at validation time).

The Agent Registry Graph API is in preview, so the model tolerates new fields
(``extra="allow"`` via :class:`RawRecord`) and surfaces them as schema drift
(AC-1.4-5). Unknown ``agent_type`` values are treated as malformed and rejected
(AC-1.4-3) -- drift tolerance applies to *extra fields*, not to enum domains.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from schemas.base import RawRecord


class AgentType(str, Enum):
    copilot_studio = "copilot_studio"
    agent_builder = "agent_builder"
    custom_engine = "custom_engine"
    sharepoint = "sharepoint"


class RawAgentPackage(RawRecord):
    package_id: str  # canonical agent key (ADR-003) -- required => AC-1.4-6 gate
    agent_type: AgentType
    name: str | None = None
    description: str | None = None
    publisher_email: str | None = None
    created: str | None = None
    last_modified: str | None = None
    deployment_status: str | None = None
    enabled: bool | None = None
    dlp_compliant: bool | None = None
    connectors: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
