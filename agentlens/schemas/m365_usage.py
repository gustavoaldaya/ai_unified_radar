"""Schema for ext-m365-usage (M1.5). Graph Reports getCopilotAgentUsage."""

from __future__ import annotations

from schemas.base import RawRecord


class RawAgentUsage(RawRecord):
    package_id: str  # FK to RawAgentPackage (required => quality gate)
    report_date: str
    agent_name: str | None = None
    active_users: int | None = None
    total_message_count: int | None = None
    agent_type: str | None = None  # Declarative / SharePoint / CustomEngine (preview)
    last_activity: str | None = None
    is_anonymized: bool = False  # tenant concealment flag (AC-1.5-4)
