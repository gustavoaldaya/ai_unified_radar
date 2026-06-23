# AgentLens — B3 / M1.4: ext-m365-registry

First concrete `BaseExtractor` subclass. Additive overlay onto B1+B2 (same
`extractors/ schemas/ tests/` layout). Runs against fixtures
(`USE_FIXTURES=true`); the live Graph path is gated on the Service Principal.

## Files

```
schemas/m365_registry.py             RawAgentPackage (frozen, extra="allow")
extractors/m365_registry.py          M365RegistryExtractor (catalog pagination)
tests/test_m365_registry.py          AC-1.4-1 … AC-1.4-6 (8 tests)
tests/fixtures/ext-m365-registry/    2-page catalog snapshot (dup + drift + malformed)
```

## Acceptance criteria → test

| AC | What | Where |
|----|------|-------|
| 1.4-1 | extends `BaseExtractor`, reuses the base pipeline | `test_extends_base_extractor` |
| 1.4-2 | full catalog pagination across >1 page (`@odata.nextLink`) | `test_pagination_follows_nextlink` (+ `test_pagination_retries_on_429`) |
| 1.4-3 | `RawAgentPackage` validates + rejects malformed (null id, bad enum) | `test_schema_validates_and_rejects_malformed` |
| 1.4-4 | daily snapshot written under `raw/m365-registry/{date}/` | `test_end_to_end_snapshot` |
| 1.4-5 | unmapped fields logged as drift without breaking | `test_end_to_end_snapshot` (`_drift` col) |
| 1.4-6 | `package_id` present + non-null in 100% of records (quality gate) | `test_end_to_end_snapshot` (null_count == 0) + `test_quarantine_contains_null_id` |

The AC-1.4-6 gate is enforced **structurally**: `package_id` is a required
field, so any null/missing id fails validation and is isolated in
`_quarantine/` instead of reaching the snapshot.

## Live endpoint + empirical caveat (ADR-K23)

Frozen M1.4 spec endpoint: `GET /beta/copilot/admin/catalog/packages` (OData
`@odata.nextLink` pagination). The ADR-K23 spike (`spike-agent-registry-graph-fields`,
run 2026-06-13 on a production tenant) found this **Catalog branch is gated by
an Agent 365 license** — HTTP 403 *"Customer must be a licensed for Agent 365…"*
(MC1173195) — while `/beta/agentRegistry/agentInstances` answers 200 with
`Directory.Read.All` and no gate. So the extractor:

* keeps the spec endpoint as the default, overridable via `GRAPH_REGISTRY_URL`
  (cutover can point at the un-gated branch per ADR-K23's adapter-based design);
* raises `AgentRegistryLicenseError` with an actionable message on 403;
* fetches the Graph token via `DefaultAzureCredential` (MI/SP), scope
  `https://graph.microsoft.com/.default`.

`_http_get_json` / `_access_token` are overridable seams so pagination is tested
without network (see the live-path tests). No new runtime dependency (stdlib
`urllib`); add `pytest` as a dev dep if not already present.

## Run it

```bash
USE_FIXTURES=true uv run pytest -q tests/test_m365_registry.py
```

## Notes

* `source_path = "m365-registry"` follows AC-1.4-4 verbatim. `Storage
  Architecture`'s folder tree shows `m365/agent_registry`; reconcile the two
  notes when convenient (raw layout is internal, not a blocker).
* `connectors[]` / `permissions[]` land as JSON-string columns (base `_coerce`);
  `enabled` / `dlp_compliant` as nullable bool. `_drift` holds unmapped fields.
* Next: **B4** (ext-m365-usage, M1.5 — 3-day moving window for late-arriving
  Graph Reports), then B5–B6.
```
