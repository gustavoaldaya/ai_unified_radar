# ADR-003: Agent as Minimum Control Unit

- **Status:** accepted
- **Date:** 2026-06-17

**Choice:** The Agent ID is the primary key of the entire data model, not Model or User.

**Rationale:** Business mental model. Leadership asks "how much does Agent X cost?" The agent is the unit of deployment, governance, and accountability.

**Risk:** Some telemetry (raw model invocations) doesn't carry agent ID. Requires deployment-to-agent mapping table with manual curation.

---
> Canonical source: AgentLens design notes in the Obsidian vault (`AI_Observability/`). This file is a Git-tracked mirror for traceability.
