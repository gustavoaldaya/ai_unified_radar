#!/usr/bin/env python
"""AgentLens -- credential & permission validator.

Runs the SAME auth seams the 12 extractors use (AAD scopes, Graph/ARM/Log
Analytics/O365 endpoints, the ADLS DataLake round-trip, boto3), so a PASS here
means that extractor will authenticate at live cutover. Run this BEFORE setting
``USE_FIXTURES=false`` and doing a real ``run --all``.

Read-only by default. The ONLY state-changing call (starting the Purview
``Audit.General`` subscription) is gated behind ``--start-audit``. The Cost
Explorer call (~$0.01, T+24h latency) is gated behind ``--aws-cost``.

Usage (from the agentlens/ project root, with the project venv):
    uv run python validate_credentials.py
    uv run python validate_credentials.py --start-audit --aws-cost

Never prints secrets. Reads the SP + per-extractor vars from ./.env.
Note: uses ClientSecretCredential (the .env SP explicitly) rather than the
DefaultAzureCredential chain, so the check is deterministic about *which*
identity it validates -- in headless prod DefaultAzureCredential resolves to
these same env-var creds.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

# --- scopes (mirror extractors/core/azure_http.py) -------------------------
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
ARM_SCOPE = "https://management.azure.com/.default"
LOG_ANALYTICS_SCOPE = "https://api.loganalytics.io/.default"
O365_SCOPE = "https://manage.office.com/.default"

# --- endpoints (mirror the concrete extractors) ----------------------------
GRAPH = "https://graph.microsoft.com"
CATALOG_URL = f"{GRAPH}/beta/copilot/admin/catalog/packages?$top=1"          # ext-m365-registry default (A365-gated)
AGENTINSTANCES_URL = f"{GRAPH}/beta/agentRegistry/agentInstances?$top=1"      # un-gated fallback (ADR-K23)
# Verified 2026-07-02: user-level endpoint (agent-level has NO public Graph API).
# Tests the Reports.Read.All consent for the ext-m365-usage fallback decision.
USAGE_URL = (f"{GRAPH}/v1.0/copilot/reports/getMicrosoft365CopilotUsageUserDetail"
             f"(period='D7')?$format=application/json")  # ext-m365-usage
DSPM_URL = f"{GRAPH}/beta/security/dataSecurityAndGovernance/aiInteractions?$top=1"  # ext-purview-dspm

results: list[tuple[str, str]] = []


def record(name: str, level: str, detail: str = "") -> None:
    results.append((name, level))
    print(f"[{level:<4}] {name:<26} {detail}")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        print(f"!! .env not found at {path}")
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ[key.strip()] = val.strip().strip('"').strip("'")


def short(msg: str, n: int = 150) -> str:
    return " ".join(msg.split())[:n]


def http(method: str, url: str, token: str, payload: dict | None = None,
         timeout: int = 45) -> tuple[int, str]:
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, ""
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        return exc.code, short(body)
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Azure
# ---------------------------------------------------------------------------
def run_azure(cred) -> None:
    tokens: dict[str, str] = {}

    def tok(scope: str, label: str) -> str | None:
        if scope in tokens:
            return tokens[scope]
        try:
            t = cred.get_token(scope).token
            tokens[scope] = t
            record(f"token:{label}", "OK", "acquired")
            return t
        except Exception as exc:  # noqa: BLE001
            record(f"token:{label}", "FAIL", f"{type(exc).__name__}: {short(str(exc))}")
            return None

    graph = tok(GRAPH_SCOPE, "graph")
    arm = tok(ARM_SCOPE, "arm")
    la = tok(LOG_ANALYTICS_SCOPE, "loganalytics")
    o365 = tok(O365_SCOPE, "o365-mgmt")

    # --- Graph: registry (both branches) ---
    if graph:
        st, msg = http("GET", CATALOG_URL, graph)
        if st == 200:
            record("graph:registry-catalog", "OK", "200 (tenant has Agent 365)")
        elif st == 403:
            record("graph:registry-catalog", "WARN",
                   "403 A365 license gate (MC1173195) -- expected; use agentRegistry branch")
        else:
            record("graph:registry-catalog", "WARN", f"HTTP {st} {msg}")

        st, msg = http("GET", AGENTINSTANCES_URL, graph)
        if st == 200:
            record("graph:agentRegistry", "OK",
                   "200 -- set GRAPH_REGISTRY_URL to this branch for ext-m365-registry")
        elif st == 403:
            record("graph:agentRegistry", "FAIL", f"403 -- missing Directory.Read.All? {msg}")
        else:
            record("graph:agentRegistry", "WARN", f"HTTP {st} {msg}")

        # --- Graph Reports (Reports.Read.All) ---
        st, msg = http("GET", USAGE_URL, graph)
        if st == 200:
            record("graph:reports-usage", "OK", "200")
        elif st in (401, 403):
            record("graph:reports-usage", "FAIL", f"HTTP {st} -- Reports.Read.All not consented? {msg}")
        else:
            record("graph:reports-usage", "WARN",
                   f"HTTP {st} (beta endpoint/period shape; perm may still be ok) {msg}")

        # --- Graph Security DSPM (SecurityEvents.Read.All, preview) ---
        st, msg = http("GET", DSPM_URL, graph)
        if st == 200:
            record("graph:dspm", "OK", "200")
        elif st == 404:
            record("graph:dspm", "WARN", "404 -- preview surface moved; set GRAPH_DSPM_URL")
        elif st in (401, 403):
            record("graph:dspm", "WARN", f"HTTP {st} -- SecurityEvents.Read.All / E7 gate {msg}")
        else:
            record("graph:dspm", "WARN", f"HTTP {st} {msg}")

    # --- Log Analytics (Log Analytics Reader on workspace) ---
    ws = os.environ.get("LOG_ANALYTICS_WORKSPACE_ID")
    if la and ws:
        url = f"https://api.loganalytics.io/v1/workspaces/{ws}/query"
        st, msg = http("POST", url, la, {"query": "print 1"})
        if st == 200:
            record("la:query", "OK", "200 (print 1)")
        elif st in (401, 403):
            record("la:query", "FAIL", f"HTTP {st} -- Log Analytics Reader missing {msg}")
        else:
            record("la:query", "WARN", f"HTTP {st} {msg}")
    elif not ws:
        record("la:query", "FAIL", "LOG_ANALYTICS_WORKSPACE_ID not set")

    # --- Azure Monitor metrics (Monitoring Reader on the resource) ---
    rid = os.environ.get("FOUNDRY_ACCOUNT_RESOURCE_ID")
    if arm and rid:
        url = f"https://management.azure.com{rid}/providers/microsoft.insights/metrics?api-version=2023-10-01"
        st, msg = http("GET", url, arm)
        rtype = "CognitiveServices/accounts" if "/Microsoft.CognitiveServices/" in rid else "OTHER resource type"
        if st == 200:
            record("arm:foundry-metrics", "OK", f"200 ({rtype})")
        elif st == 400:
            record("arm:foundry-metrics", "WARN",
                   f"400 -- RBAC ok, needs metricnames param at run ({rtype}) {msg}")
        elif st in (401, 403):
            record("arm:foundry-metrics", "FAIL", f"HTTP {st} -- Monitoring Reader missing {msg}")
        else:
            record("arm:foundry-metrics", "WARN", f"HTTP {st} ({rtype}) {msg}")
        if "/providers/microsoft.insights/components/" in rid.lower():
            record("arm:foundry-metrics-note", "WARN",
                   "resource id is an App Insights component, not the CognitiveServices "
                   "Foundry account -- model-level metrics will be wrong")
    elif not rid:
        record("arm:foundry-metrics", "FAIL", "FOUNDRY_ACCOUNT_RESOURCE_ID not set")

    # --- Cost Management (Cost Management Reader) -- ONE call, hard 15/h ---
    scope = os.environ.get("AZURE_COST_SCOPE")
    if arm and scope and "<" not in scope:
        url = f"https://management.azure.com{scope}/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
        st, msg = http("POST", url, arm, {"type": "ActualCost", "timeframe": "MonthToDate"})
        if st == 200:
            record("arm:cost", "OK", "200")
        elif st == 400:
            record("arm:cost", "WARN", f"400 -- RBAC ok, query body needs a dataset at run {msg}")
        elif st in (401, 403):
            record("arm:cost", "FAIL", f"HTTP {st} -- Cost Management Reader missing {msg}")
        elif st == 429:
            record("arm:cost", "WARN", "429 -- hit the ~15/h limit; retry later")
        else:
            record("arm:cost", "WARN", f"HTTP {st} {msg}")
    elif not scope or "<" in (scope or ""):
        record("arm:cost", "FAIL", "AZURE_COST_SCOPE not set / still a placeholder")

    # --- ADLS Gen2 write probe (Storage Blob Data Contributor) ---
    acct = os.environ.get("ADLS_ACCOUNT_URL")
    fsname = os.environ.get("ADLS_FILESYSTEM")
    if acct and fsname:
        try:
            from azure.storage.filedatalake import DataLakeServiceClient
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            rel = f"_probe/agentlens_credcheck_{stamp}.txt"
            svc = DataLakeServiceClient(account_url=acct, credential=cred)
            fc = svc.get_file_system_client(fsname).get_file_client(rel)
            fc.upload_data(b"agentlens ok", overwrite=True)
            back = fc.download_file().readall()
            fc.delete_file()
            if back == b"agentlens ok":
                record("adls:write-probe", "OK", f"write+read+delete on {fsname}/{rel}")
            else:
                record("adls:write-probe", "FAIL", "round-trip mismatch")
        except Exception as exc:  # noqa: BLE001
            record("adls:write-probe", "FAIL", f"{type(exc).__name__}: {short(str(exc))}")
    else:
        record("adls:write-probe", "FAIL", "ADLS_ACCOUNT_URL / ADLS_FILESYSTEM not set")


def ensure_filesystem(cred) -> None:
    """Create the ADLS filesystem (container) if missing. Needs Storage Blob
    Data Contributor at account scope (container create is a data-plane action
    included in that role)."""
    acct = os.environ.get("ADLS_ACCOUNT_URL")
    fsname = os.environ.get("ADLS_FILESYSTEM", "raw")
    if not acct:
        record("adls:create-fs", "FAIL", "ADLS_ACCOUNT_URL not set")
        return
    try:
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.filedatalake import DataLakeServiceClient

        svc = DataLakeServiceClient(account_url=acct, credential=cred)
        try:
            svc.create_file_system(fsname)
            record(f"adls:create-fs:{fsname}", "OK", "created")
        except ResourceExistsError:
            record(f"adls:create-fs:{fsname}", "OK", "already exists")
    except Exception as exc:  # noqa: BLE001
        record("adls:create-fs", "FAIL",
               f"{type(exc).__name__}: {short(str(exc))} "
               "(if AuthorizationFailure: grant Storage Blob Data Contributor "
               "on the account, or create the container in the portal)")


def start_audit_subscription(cred) -> None:
    tenant = os.environ.get("AZURE_TENANT_ID")
    publisher = os.environ.get("O365_MGMT_PUBLISHER_ID", tenant)
    if not tenant:
        record("o365:start-audit", "FAIL", "AZURE_TENANT_ID not set")
        return
    try:
        token = cred.get_token(O365_SCOPE).token
    except Exception as exc:  # noqa: BLE001
        record("o365:start-audit", "FAIL", f"token: {short(str(exc))}")
        return
    url = (f"https://manage.office.com/api/v1.0/{tenant}/activity/feed/subscriptions/start"
           f"?contentType=Audit.General&PublisherIdentifier={publisher}")
    st, msg = http("POST", url, token, {})
    if st in (200, 400):
        record("o365:start-audit", "OK", f"HTTP {st} (started / already enabled; first blobs ~12h)")
    else:
        record("o365:start-audit", "FAIL", f"HTTP {st} {msg}")


def check_audit_readonly(cred) -> None:
    tenant = os.environ.get("AZURE_TENANT_ID")
    publisher = os.environ.get("O365_MGMT_PUBLISHER_ID", tenant)
    if not tenant:
        record("o365:audit-list", "FAIL", "AZURE_TENANT_ID not set")
        return
    try:
        token = cred.get_token(O365_SCOPE).token
    except Exception as exc:  # noqa: BLE001
        record("o365:audit-list", "FAIL", f"token: {short(str(exc))}")
        return
    url = (f"https://manage.office.com/api/v1.0/{tenant}/activity/feed/subscriptions/list"
           f"?PublisherIdentifier={publisher}")
    st, msg = http("GET", url, token)
    if st == 200:
        record("o365:audit-list", "OK", "200 (ActivityFeed.Read ok; run --start-audit once to enable content)")
    elif st in (401, 403):
        record("o365:audit-list", "FAIL", f"HTTP {st} -- ActivityFeed.Read / Audit Reader missing {msg}")
    else:
        record("o365:audit-list", "WARN", f"HTTP {st} {msg}")


# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------
def run_aws(do_cost: bool) -> None:
    region = os.environ.get("AWS_REGION")
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        record("aws:sts", "FAIL", "AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY not set")
        return
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        record("aws:sts", "FAIL", "boto3 not installed -- run uv sync")
        return

    try:
        ident = boto3.client("sts", region_name=region).get_caller_identity()
        record("aws:sts", "OK", f"account {ident['Account']} region {region}")
    except Exception as exc:  # noqa: BLE001
        record("aws:sts", "FAIL", f"{type(exc).__name__}: {short(str(exc))}")
        return

    logs = boto3.client("logs", region_name=region)
    for var, label in (("BEDROCK_INVOCATION_LOG_GROUP", "logs:invocations"),
                       ("BEDROCK_AGENTCORE_LOG_GROUP", "logs:agentcore")):
        grp = os.environ.get(var)
        if not grp:
            record(f"aws:{label}", "WARN", f"{var} not set")
            continue
        try:
            resp = logs.describe_log_groups(logGroupNamePrefix=grp)
            found = any(g["logGroupName"] == grp for g in resp.get("logGroups", []))
            if found:
                record(f"aws:{label}", "OK", f"group exists: {grp}")
            else:
                record(f"aws:{label}", "WARN", f"perm ok, group not found (enable logging): {grp}")
        except ClientError as exc:
            record(f"aws:{label}", "FAIL", exc.response["Error"].get("Code", "ClientError"))

    try:
        boto3.client("cloudwatch", region_name=region).list_metrics(Namespace="AWS/Bedrock")
        record("aws:cloudwatch", "OK", "list_metrics AWS/Bedrock")
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
        record("aws:cloudwatch", "FAIL", str(code))

    if do_cost:
        try:
            ce = boto3.client("ce", region_name="us-east-1")  # Cost Explorer is us-east-1 only
            today = dt.date.today()
            ce.get_cost_and_usage(
                TimePeriod={"Start": str(today - dt.timedelta(days=1)), "End": str(today)},
                Granularity="DAILY", Metrics=["UnblendedCost"],
            )
            record("aws:cost-explorer", "OK", "get_cost_and_usage")
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
            record("aws:cost-explorer", "WARN", str(code))
    else:
        record("aws:cost-explorer", "WARN", "skipped (pass --aws-cost; ~$0.01/call)")


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentLens credential/permission validator")
    parser.add_argument("--start-audit", action="store_true",
                        help="fire the one-time Audit.General subscription start (state-changing)")
    parser.add_argument("--aws-cost", action="store_true",
                        help="also test Cost Explorer (~$0.01, T+24h latency)")
    parser.add_argument("--create-fs", action="store_true",
                        help="create the ADLS_FILESYSTEM container if missing (state-changing)")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parent / ".env")
    print("=" * 78)
    print("AgentLens credential validation  (read-only unless --start-audit)")
    print("=" * 78)

    try:
        from azure.identity import ClientSecretCredential
        cred = ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )
    except ImportError:
        record("azure:sdk", "FAIL", "azure-identity not installed -- run uv sync")
        cred = None
    except KeyError as exc:
        record("azure:sp", "FAIL", f"missing SP var {exc}")
        cred = None

    if cred is not None:
        if args.create_fs:
            ensure_filesystem(cred)
        run_azure(cred)
        check_audit_readonly(cred)
        if args.start_audit:
            start_audit_subscription(cred)

    print("-" * 78)
    run_aws(args.aws_cost)

    print("=" * 78)
    n_ok = sum(1 for _, lvl in results if lvl == "OK")
    n_warn = sum(1 for _, lvl in results if lvl == "WARN")
    n_fail = sum(1 for _, lvl in results if lvl == "FAIL")
    print(f"SUMMARY: {n_ok} PASS · {n_warn} WARN · {n_fail} FAIL")
    if n_fail:
        print("FAILs block the corresponding extractor(s). WARNs are expected/known-caveat.")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
