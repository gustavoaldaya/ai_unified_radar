# CHANGELOG fragment — merge into agentlens/CHANGELOG.md

## [0.3.0-alpha] — 2026-06-18

### Added
- `ext-m365-registry` (M1.4): first concrete `BaseExtractor` subclass.
  - `schemas/m365_registry.py` — `RawAgentPackage` (canonical `package_id`,
    `agent_type` enum, connectors/permissions, drift-tolerant).
  - `extractors/m365_registry.py` — `M365RegistryExtractor` with OData
    `@odata.nextLink` catalog pagination, 429 retry, and an explicit 403
    A365-license-gate guard (`AgentRegistryLicenseError`, ADR-K23).
  - 8 tests (AC-1.4-1 … AC-1.4-6) + 2-page fixture snapshot with an intentional
    duplicate, schema drift, and a malformed (null `package_id`) record.

### Notes
- Suggested tag: `agentlens-v0.3.0-alpha` (SemVer minor).
- AC-1.4-6 gate enforced structurally via required `package_id`.
