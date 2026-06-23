"""Schema for ext-m365-user-activity. Graph Reports general usage."""

from __future__ import annotations

from schemas.base import RawRecord


class RawUserActivity(RawRecord):
    user_id: str
    report_date: str
    product: str | None = None  # Teams / SharePoint / Exchange / OneDrive / Yammer
    activity_count: int | None = None
    is_active: bool | None = None
    is_anonymized: bool = False
