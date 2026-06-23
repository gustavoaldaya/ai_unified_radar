# ADR-001: Batch over Streaming

- **Status:** accepted
- **Date:** 2026-06-17

**Choice:** Batch extraction (hourly/daily) rather than real-time streaming.

**Rationale:** Primary consumers are FinOps dashboards and governance reports — none require sub-hour latency. Batch simplifies error handling, reduces infrastructure cost, and avoids exactly-once semantics complexity across multi-cloud streams. 4-hour SLA is generous.

**Risk:** If real-time agent health alerting becomes a need, add a streaming path (Event Hub → Stream Analytics) alongside batch.

---
> Canonical source: AgentLens design notes in the Obsidian vault (`AI_Observability/`). This file is a Git-tracked mirror for traceability.
