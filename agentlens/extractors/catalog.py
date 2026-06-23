"""Central catalog of all v1 extractors (name -> class).

Used by the runner/orchestrator. Keeps the 12-extractor inventory from
``architecture/Extractor Architecture`` in one importable place.
"""

from __future__ import annotations

from extractors.base import BaseExtractor
from extractors.bedrock_cost import BedrockCostExtractor
from extractors.bedrock_invocations import BedrockInvocationsExtractor
from extractors.bedrock_metrics import BedrockMetricsExtractor
from extractors.bedrock_traces import BedrockTracesExtractor
from extractors.foundry_cost import FoundryCostExtractor
from extractors.foundry_metrics import FoundryMetricsExtractor
from extractors.foundry_traces import FoundryTracesExtractor
from extractors.m365_registry import M365RegistryExtractor
from extractors.m365_usage import M365UsageExtractor
from extractors.m365_user_activity import M365UserActivityExtractor
from extractors.purview_audit import PurviewAuditExtractor
from extractors.purview_dspm import PurviewDspmExtractor

EXTRACTOR_CLASSES: tuple[type[BaseExtractor], ...] = (
    M365RegistryExtractor,
    M365UsageExtractor,
    M365UserActivityExtractor,
    PurviewAuditExtractor,
    PurviewDspmExtractor,
    FoundryTracesExtractor,
    FoundryMetricsExtractor,
    FoundryCostExtractor,
    BedrockInvocationsExtractor,
    BedrockMetricsExtractor,
    BedrockTracesExtractor,
    BedrockCostExtractor,
)

CATALOG: dict[str, type[BaseExtractor]] = {cls.name: cls for cls in EXTRACTOR_CLASSES}


def get_extractor(name: str) -> type[BaseExtractor]:
    if name not in CATALOG:
        raise KeyError(f"unknown extractor '{name}'; known: {sorted(CATALOG)}")
    return CATALOG[name]
