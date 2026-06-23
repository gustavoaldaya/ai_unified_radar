# ADR-006: Optimizations Ship with Data

- **Status:** accepted
- **Date:** 2026-06-17

**Choice:** FinOps, security, and design optimization engines run as part of the daily transform pipeline in v1.

**Rationale:** Optimization layer is lightweight SQL/PySpark rules. Deferring means infrastructure without actionable insights. Signals are simple (Z-score, thresholds, string similarity) and high business value.

**Risk:** First 30 days noisy baselines. Mitigation: marked `baseline_period = TRUE`, excluded from alerting.

**Signal Types:**
- **FinOps:** Cost anomaly Z-scores, idle agents, model right-sizing, PTU utilization alerts
- **Security:** DLP violation trends, risk level changes, label breach patterns
- **Design:** Duplicate agent detection, orphan deployments, metadata gap reporting

---
> Canonical source: AgentLens design notes in the Obsidian vault (`AI_Observability/`). This file is a Git-tracked mirror for traceability.
