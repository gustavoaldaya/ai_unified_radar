"""Base Pydantic schema for all raw-zone records.

Every extractor's payload schema (``RawAgentPackage``, ``RawAgentUsage``, ...)
subclasses :class:`RawRecord`. Two project-wide decisions live here:

* ``frozen=True``  -- raw records are immutable once parsed.
* ``extra="allow"`` -- preview APIs (Agent Registry) add fields without notice;
  we keep them and surface them as a **schema-drift signal** (see
  :meth:`RawRecord.drift_fields`) instead of failing validation. This is the
  ``extra="allow"`` tolerance required by AC-1.4-5 and reused by every extractor.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict


class RawRecord(BaseModel):
    """Immutable, drift-tolerant base for raw-zone schemas."""

    model_config = ConfigDict(frozen=True, extra="allow")

    @property
    def drift_fields(self) -> dict[str, Any]:
        """Fields present in the payload but not declared on the schema."""
        return dict(self.__pydantic_extra__ or {})

    def declared_dump(self) -> dict[str, Any]:
        """Only the declared (typed) fields -- the Parquet column set."""
        return {name: getattr(self, name) for name in type(self).model_fields}

    def drift_json(self) -> str | None:
        """Drift fields serialised to a stable JSON string, or ``None``."""
        drift = self.drift_fields
        if not drift:
            return None
        return json.dumps(drift, default=str, sort_keys=True)
