# GCP Scanner Uplift — Slice 2a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add **org-level GCP onboarding** so one grant at the Organization node lets the scanner enumerate and scan every project under it. The existing single-project onboarding stays as the fallback.

**Architecture:** A new `--org <ORG_ID>` flag on `cfn/gcp/onboard.sh` runs an org-mode branch — host project for the WIF pool + reader SA, read roles granted at the Organization node, posts `mode=org` back. The webhook stores the org-mode scope (no auto-scan in org mode). The scanner enumerates projects in Stage 1 when invoked in org mode and writes the discovered list back to `cloud_connections.scope.projects`, so the picker (Slice 2b) sees the list after the first scan. `_rescan_gcp` reads `scope.mode` and routes correctly.

**Tech Stack:** Bash (Cloud Shell), Python 3.12 (Lambda), AWS CDK (TypeScript), Google Workload Identity Federation, Resource Manager API (via Shasta's GCPClient).

**Spec:** `docs/superpowers/specs/2026-05-22-gcp-scanner-uplift-design.md` §6
**Predecessors (merged):** Slices 1a + 1b. The v2 Fargate scanner is live; single-project onboarding works end-to-end.

---

## Background an implementer needs

- **The spec called for a hybrid (webhook enumeration + scanner refresh).** This plan implements *scan-time-only* enumeration as a focused scope cut: the webhook just stores credentials, and the scanner enumerates on every scan. Trade-off: picker is empty until the first scan completes (~3-5 min). Reason: enumerating in the webhook requires bundling `google-auth` + `google-api-python-client` into a Lambda that's currently dep-free, *plus* solving a non-trivial IAM puzzle (the webhook's role must be allowed to assume `gcpScannerRole` so its GetCallerIdentity matches the WIF principalSet). Scanner-side enumeration reuses the existing Shasta `GCPClient.list_projects()` and the existing IAM setup with zero new dependencies. The hybrid can be revisited later.
- **The new scope shape for org-mode connections** stored in `cloud_connections.scope`:
  ```json
  {
    "mode":                "org",
    "org_id":              "<numeric org id>",
    "host_project_id":     "<gcp project>",
    "host_project_number": "<gcp project number>",
    "sa_email":            "ciso-copilot-reader@<host>.iam.gserviceaccount.com",
    "wif_pool":            "ciso-copilot-pool",
    "wif_provider":        "ciso-copilot-aws-provider",
    "projects":            {},
    "selected":            []
  }
  ```
  The existing single-project shape stays exactly as-is (no `mode` key → treated as project mode for backward compatibility).
- **Slice 1a's `project_discovery.py` deliberately deferred `enumerate_projects`.** This plan adds it (Task 3).
- **The Fargate task already passes its env-var contract through `run.py`.** This plan adds two optional vars (`MODE`, allowing `PROJECT_IDS` to be empty in org mode).
- **No CDK change to the API stack** is required for 2a — the rescan + onboarding trigger paths were already rewired in 1b to use Fargate. Only the scan-stack scanner image needs a rebuild after Tasks 3-5.
- **Live verification is human-gated.** The org `onboard.sh` must be run by someone with **org-admin** on a real GCP Organization. The plan documents the verification steps but cannot execute them.

## File structure

```
platform/cfn/gcp/onboard.sh                                MODIFIED — add --org flag + org-mode branch
platform/lambda/onboarding_gcp_complete/main.py            MODIFIED — branch on `mode`; org mode skips auto-scan
platform/lambda/connections_list/main.py                   MODIFIED — _rescan_gcp routes on scope.mode
platform/lambda/shasta_runner_gcp/app/project_discovery.py MODIFIED — add enumerate_projects()
platform/lambda/shasta_runner_gcp/app/project_discovery_tests…  MODIFIED — add tests for enumerate_projects()
platform/lambda/shasta_runner_gcp/app/main.py              MODIFIED — Stage 1 enumeration + scope.projects writeback in org mode
platform/lambda/shasta_runner_gcp/app/run.py               MODIFIED — MODE env var, PROJECT_IDS optional in org mode
platform/lambda/shasta_runner_gcp/app/tests/test_run.py    MODIFIED — cover MODE + empty PROJECT_IDS
HANDOFF.md                                                 MODIFIED — record Slice 2a shipped
```

---

### Task 1: `cfn/gcp/onboard.sh` — add `--org <ORG_ID>` mode

**File:** `platform/cfn/gcp/onboard.sh`

The existing script is project-mode by default. Add a `--org` flag that switches to org-mode: roles are bound at the Organization node, the host project still hosts the WIF pool + SA, and the POST body carries `mode=org` + the org metadata.

- [ ] **Step 1: Replace the arg-parsing block**

Find the existing arg block (around lines 26-34):

```bash
EXTERNAL_ID="${1:-}"
if [[ -z "$EXTERNAL_ID" ]]; then
  echo "ERROR: external ID required. Run as:" >&2
  echo "  curl -fsSL https://cdn.settlingforless.com/gcp/onboard.sh | bash -s -- <EXTERNAL_ID>" >&2
  exit 1
fi
```

Replace it with (supports both `--org <ORG_ID>` and the positional `<EXTERNAL_ID>`):

```bash
# Args:
#   $1                    EXTERNAL_ID (required)
#   --org <ORG_ID>        switch to org-mode (binds reader roles at the Organization
#                         node, posts mode=org). Without --org, the script runs in
#                         single-project mode (the historical behaviour).
EXTERNAL_ID=""
ORG_ID=""
while (( $# > 0 )); do
  case "$1" in
    --org)
      ORG_ID="${2:-}"; shift 2 ;;
    --org=*)
      ORG_ID="${1#--org=}"; shift ;;
    -h|--help)
      echo "Usage: onboard.sh <EXTERNAL_ID> [--org <ORG_ID>]"; exit 0 ;;
    *)
      if [[ -z "$EXTERNAL_ID" ]]; then EXTERNAL_ID="$1"; else
        echo "ERROR: unexpected arg: $1" >&2; exit 1
      fi
      shift ;;
  esac
done

if [[ -z "$EXTERNAL_ID" ]]; then
  echo "ERROR: external ID required. Run as:" >&2
  echo "  curl -fsSL https://cdn.settlingforless.com/gcp/onboard.sh | bash -s -- <EXTERNAL_ID>" >&2
  echo "Add --org <ORG_ID> to scan every project under a GCP organisation:" >&2
  echo "  curl -fsSL https://.../onboard.sh | bash -s -- <EXTERNAL_ID> --org 123456789012" >&2
  exit 1
fi

MODE="project"
if [[ -n "$ORG_ID" ]]; then
  MODE="org"
fi
```

- [ ] **Step 2: Add org-mode header echo + role bindings**

Find the existing header echo block (around lines 53-56):

```bash
echo "Project:        $PROJECT_ID ($PROJECT_NUMBER)"
echo "Pool / Provider: $POOL_ID / $PROVIDER_ID"
echo "Service account: $SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
echo
```

Replace with (adds the mode + org context to the header):

```bash
echo "Mode:            $MODE"
if [[ "$MODE" == "org" ]]; then
  echo "Organization:    $ORG_ID"
  echo "Host project:    $PROJECT_ID ($PROJECT_NUMBER)"
else
  echo "Project:         $PROJECT_ID ($PROJECT_NUMBER)"
fi
echo "Pool / Provider: $POOL_ID / $PROVIDER_ID"
echo "Service account: $SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
echo
```

- [ ] **Step 3: Replace the role-grant block to support org mode**

Find the existing per-project role-binding block (around lines 110-116):

```bash
echo "==> granting read-only roles"
for ROLE in roles/iam.securityReviewer roles/cloudasset.viewer roles/logging.viewer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" --role="$ROLE" \
    --condition=None --quiet >/dev/null 2>&1 || \
    echo "  warn: failed to bind $ROLE (proceeding)"
done
```

Replace it with (the loop now binds at the org for org-mode, and includes `roles/browser` so `projects.search` works org-wide):

```bash
if [[ "$MODE" == "org" ]]; then
  echo "==> granting read-only roles at the organisation ($ORG_ID)"
  for ROLE in roles/iam.securityReviewer roles/cloudasset.viewer \
              roles/logging.viewer roles/browser; do
    gcloud organizations add-iam-policy-binding "$ORG_ID" \
      --member="serviceAccount:$SA_EMAIL" --role="$ROLE" \
      --condition=None --quiet >/dev/null 2>&1 || \
      echo "  warn: failed to bind $ROLE at org (proceeding)"
  done
else
  echo "==> granting read-only roles at the project"
  for ROLE in roles/iam.securityReviewer roles/cloudasset.viewer roles/logging.viewer; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:$SA_EMAIL" --role="$ROLE" \
      --condition=None --quiet >/dev/null 2>&1 || \
      echo "  warn: failed to bind $ROLE (proceeding)"
  done
fi
```

- [ ] **Step 4: Add `mode` and `org_id` to the POST body**

Find the existing POST-body construction (around lines 130-137):

```bash
POST_BODY="$(jq -nc \
  --arg eid     "$EXTERNAL_ID" \
  --arg pid     "$PROJECT_ID" \
  --arg pnum    "$PROJECT_NUMBER" \
  --arg sa      "$SA_EMAIL" \
  --arg pool    "$POOL_ID" \
  --arg provider "$PROVIDER_ID" \
  '{external_id:$eid, project_id:$pid, project_number:$pnum, sa_email:$sa, wif_pool:$pool, wif_provider:$provider}')"
```

Replace with (adds `mode` and `org_id` when in org mode; the field names match what the webhook reads in Task 2):

```bash
if [[ "$MODE" == "org" ]]; then
  POST_BODY="$(jq -nc \
    --arg eid      "$EXTERNAL_ID" \
    --arg mode     "$MODE" \
    --arg org      "$ORG_ID" \
    --arg pid      "$PROJECT_ID" \
    --arg pnum     "$PROJECT_NUMBER" \
    --arg sa       "$SA_EMAIL" \
    --arg pool     "$POOL_ID" \
    --arg provider "$PROVIDER_ID" \
    '{external_id:$eid, mode:$mode, org_id:$org, host_project_id:$pid, host_project_number:$pnum, sa_email:$sa, wif_pool:$pool, wif_provider:$provider}')"
else
  POST_BODY="$(jq -nc \
    --arg eid      "$EXTERNAL_ID" \
    --arg pid      "$PROJECT_ID" \
    --arg pnum     "$PROJECT_NUMBER" \
    --arg sa       "$SA_EMAIL" \
    --arg pool     "$POOL_ID" \
    --arg provider "$PROVIDER_ID" \
    '{external_id:$eid, project_id:$pid, project_number:$pnum, sa_email:$sa, wif_pool:$pool, wif_provider:$provider}')"
fi
```

- [ ] **Step 5: Update the success message**

Find:

```bash
if [[ "$HTTP" =~ ^2 ]]; then
  echo
  echo "✓ GCP project $PROJECT_ID connected to CISO Copilot."
  echo "  Open the app — your first scan starts now."
```

Replace the inner success lines:

```bash
if [[ "$HTTP" =~ ^2 ]]; then
  echo
  if [[ "$MODE" == "org" ]]; then
    echo "✓ GCP organisation $ORG_ID connected to CISO Copilot."
    echo "  Open the app and run your first scan — projects discover on scan."
  else
    echo "✓ GCP project $PROJECT_ID connected to CISO Copilot."
    echo "  Open the app — your first scan starts now."
  fi
```

- [ ] **Step 6: Lint-check the script**

Run: `bash -n platform/cfn/gcp/onboard.sh && echo "syntax OK"`
Expected: `syntax OK`.

If `shellcheck` is available: `shellcheck platform/cfn/gcp/onboard.sh` — fix any error-level issues; warnings about quoting in existing code can be left.

- [ ] **Step 7: Commit**

```bash
git add platform/cfn/gcp/onboard.sh
git commit -m "feat: gcp onboard.sh supports --org for org-wide reader grants"
```

---

### Task 2: `onboarding_gcp_complete` — branch on `mode`

**File:** `platform/lambda/onboarding_gcp_complete/main.py`

In project mode the webhook continues to auto-scan (current behaviour). In org mode it stores the org-mode scope and does NOT auto-scan — the user runs the first scan manually, at which point the scanner enumerates projects (Task 4).

- [ ] **Step 1: Update the docstring and the body-parsing block**

Find the docstring (lines 1-15) and replace the entire docstring with:

```python
"""POST /onboarding/gcp/complete

NOT JWT-authed — called by the gcloud onboarding script. Authenticates via
the external_id matching the pending cloud_connection row.

Body (project mode — historical default):
  {
    "external_id":     "<one-time>",
    "project_id":      "<gcp project>",
    "project_number":  "<gcp project number>",
    "sa_email":        "ciso-copilot-reader@<project>.iam.gserviceaccount.com",
    "wif_pool":        "ciso-copilot-pool",
    "wif_provider":    "ciso-copilot-aws-provider"
  }

Body (org mode — when onboard.sh was run with --org <ORG_ID>):
  {
    "external_id":         "<one-time>",
    "mode":                "org",
    "org_id":              "<gcp org id>",
    "host_project_id":     "<host gcp project>",
    "host_project_number": "<host gcp project number>",
    "sa_email":            "ciso-copilot-reader@<host>.iam.gserviceaccount.com",
    "wif_pool":            "ciso-copilot-pool",
    "wif_provider":        "ciso-copilot-aws-provider"
  }

Org mode does NOT auto-scan — projects are discovered lazily on the first
scan (the scanner's Stage 1 enumerates and writes back to scope.projects).
Project mode keeps the historical auto-scan-on-onboard behaviour.
"""
```

- [ ] **Step 2: Rewrite the body-validation + scope-construction block**

Find the existing block (around lines 33-65):

```python
def handler(event: dict, context) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    external_id    = body.get("external_id")
    project_id     = body.get("project_id")
    project_number = body.get("project_number")
    sa_email       = body.get("sa_email")
    wif_pool       = body.get("wif_pool")
    wif_provider   = body.get("wif_provider")

    if not all([external_id, project_id, project_number, sa_email, wif_pool, wif_provider]):
        return _resp(400, {"error": "missing_fields"})

    conn = _get_connection_by_external_id(external_id)
    if not conn:
        return _resp(404, {"error": "external_id_unknown"})
    if conn["status"] != "pending":
        return _resp(409, {"error": "already_completed", "current_status": conn["status"]})

    # GCP WIF: no shared secret to store. Configuration (project, pool, provider,
    # SA email) is stored in `scope` JSON. The Lambda re-constructs the WIF
    # external_account credentials at invoke time from these values.
    scope = {
        "project_id":     project_id,
        "project_number": project_number,
        "sa_email":       sa_email,
        "wif_pool":       wif_pool,
        "wif_provider":   wif_provider,
    }
```

Replace it entirely with:

```python
def handler(event: dict, context) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    external_id  = body.get("external_id")
    mode         = (body.get("mode") or "project").lower()
    sa_email     = body.get("sa_email")
    wif_pool     = body.get("wif_pool")
    wif_provider = body.get("wif_provider")
    if not all([external_id, sa_email, wif_pool, wif_provider]):
        return _resp(400, {"error": "missing_fields"})

    if mode == "org":
        org_id              = body.get("org_id")
        host_project_id     = body.get("host_project_id")
        host_project_number = body.get("host_project_number")
        if not all([org_id, host_project_id, host_project_number]):
            return _resp(400, {"error": "missing_fields"})
        scope = {
            "mode":                "org",
            "org_id":              org_id,
            "host_project_id":     host_project_id,
            "host_project_number": host_project_number,
            "sa_email":            sa_email,
            "wif_pool":            wif_pool,
            "wif_provider":        wif_provider,
            "projects":            {},
            "selected":            [],
        }
        account_identifier = org_id
    elif mode == "project":
        project_id     = body.get("project_id")
        project_number = body.get("project_number")
        if not all([project_id, project_number]):
            return _resp(400, {"error": "missing_fields"})
        scope = {
            "project_id":     project_id,
            "project_number": project_number,
            "sa_email":       sa_email,
            "wif_pool":       wif_pool,
            "wif_provider":   wif_provider,
        }
        account_identifier = project_id
    else:
        return _resp(400, {"error": "invalid_mode", "mode": mode})

    conn = _get_connection_by_external_id(external_id)
    if not conn:
        return _resp(404, {"error": "external_id_unknown"})
    if conn["status"] != "pending":
        return _resp(409, {"error": "already_completed", "current_status": conn["status"]})
```

- [ ] **Step 3: Replace the UPDATE statement to use the generalised `account_identifier`**

Find (around lines 66-82):

```python
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE cloud_connections "
            "SET status = 'active', "
            "    account_identifier = :pid, "
            "    scope = CAST(:scope AS JSONB), "
            "    signals = jsonb_build_object('pull_scan', true, 'alerts', false, 'drift', false), "
            "    updated_at = now() "
            "WHERE conn_id = CAST(:cid AS UUID)"
        ),
        parameters=[
            {"name": "cid",   "value": {"stringValue": conn["conn_id"]}},
            {"name": "pid",   "value": {"stringValue": project_id}},
            {"name": "scope", "value": {"stringValue": json.dumps(scope)}},
        ],
    )

    print(f"gcp connection {conn['conn_id']} active — project {project_id}")
```

Replace with:

```python
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE cloud_connections "
            "SET status = 'active', "
            "    account_identifier = :pid, "
            "    scope = CAST(:scope AS JSONB), "
            "    signals = jsonb_build_object('pull_scan', true, 'alerts', false, 'drift', false), "
            "    updated_at = now() "
            "WHERE conn_id = CAST(:cid AS UUID)"
        ),
        parameters=[
            {"name": "cid",   "value": {"stringValue": conn["conn_id"]}},
            {"name": "pid",   "value": {"stringValue": account_identifier}},
            {"name": "scope", "value": {"stringValue": json.dumps(scope)}},
        ],
    )

    print(f"gcp connection {conn['conn_id']} active — {mode}={account_identifier}")
```

- [ ] **Step 4: Skip the auto-scan in org mode**

Find (around lines 86-96):

```python
    initial_scan_id = _run_initial_scan(
        tenant_id = conn["tenant_id"],
        conn_id   = conn["conn_id"],
        scope     = scope,
    )

    return _resp(200, {
        "status":          "active",
        "connection_id":   conn["conn_id"],
        "initial_scan_id": initial_scan_id,
    })
```

Replace with:

```python
    if mode == "org":
        # Org mode does not auto-scan — the project list is empty until the
        # scanner enumerates on first scan. The user starts the scan manually
        # (Connect-page rescan today; the /scan screen after Slice 2b).
        initial_scan_id = None
    else:
        initial_scan_id = _run_initial_scan(
            tenant_id = conn["tenant_id"],
            conn_id   = conn["conn_id"],
            scope     = scope,
        )

    return _resp(200, {
        "status":          "active",
        "connection_id":   conn["conn_id"],
        "mode":            mode,
        "initial_scan_id": initial_scan_id,
    })
```

- [ ] **Step 5: Verify the module parses**

Run: `cd platform/lambda/onboarding_gcp_complete && python3 -c "import ast; ast.parse(open('main.py').read()); print('parses OK')"`
Expected: `parses OK`.

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/onboarding_gcp_complete/main.py
git commit -m "feat: gcp onboarding accepts org mode (no auto-scan, scope stored)"
```

---

### Task 3: `project_discovery.enumerate_projects` — the scan-time helper

**File:** `platform/lambda/shasta_runner_gcp/app/project_discovery.py`
**Test file:** `platform/lambda/shasta_runner_gcp/app/tests/test_project_discovery.py`

Add the `enumerate_projects` helper that was deferred from Slice 1a. It takes a `GCPClient`-shaped object and returns `{project_id: display_name}` for every accessible project. The scanner uses this in Stage 1 in org mode.

- [ ] **Step 1: Write the failing tests**

Open `platform/lambda/shasta_runner_gcp/app/tests/test_project_discovery.py` and append the following block at the end:

```python
# ---------------------------------------------------------------------
# enumerate_projects
# ---------------------------------------------------------------------

from project_discovery import enumerate_projects


class _FakeClient:
    def __init__(self, projects):
        self._projects = projects

    def list_projects(self):
        return self._projects


def test_enumerate_returns_project_id_to_display_name():
    client = _FakeClient([
        {"project_id": "proj-a", "display_name": "Project A"},
        {"project_id": "proj-b", "display_name": "Project B"},
    ])
    assert enumerate_projects(client) == {
        "proj-a": "Project A",
        "proj-b": "Project B",
    }


def test_enumerate_falls_back_to_id_when_display_name_missing():
    client = _FakeClient([
        {"project_id": "proj-a", "display_name": ""},
        {"project_id": "proj-b"},
    ])
    assert enumerate_projects(client) == {"proj-a": "proj-a", "proj-b": "proj-b"}


def test_enumerate_skips_rows_without_project_id():
    client = _FakeClient([
        {"project_id": "proj-a", "display_name": "A"},
        {"project_id": "",       "display_name": "junk"},
        {"display_name": "no-id"},
    ])
    assert enumerate_projects(client) == {"proj-a": "A"}


def test_enumerate_empty_list_returns_empty_dict():
    client = _FakeClient([])
    assert enumerate_projects(client) == {}
```

- [ ] **Step 2: Run the new tests — verify they fail**

Run: `cd platform/lambda/shasta_runner_gcp && uv run --with pytest python -m pytest app/tests/test_project_discovery.py -v -k enumerate`
Expected: 4 ERRORs — `ImportError` because `enumerate_projects` does not exist yet.

- [ ] **Step 3: Add the implementation**

Open `platform/lambda/shasta_runner_gcp/app/project_discovery.py` and append the following at the end of the file:

```python


def enumerate_projects(client) -> dict[str, str]:
    """Return {project_id: display_name} for every project accessible to
    `client.list_projects()`. Used by the scanner in org mode to refresh
    the connection's `scope.projects` before scanning.

    `client.list_projects()` returns a list of dicts with at least
    `project_id` and (optional) `display_name`. Rows without a
    project_id are skipped; a missing/empty display_name falls back to
    the project_id itself.

    Pure — `client` is duck-typed so the function stays unit-testable
    without the Google SDK."""
    out: dict[str, str] = {}
    for row in client.list_projects():
        pid = (row.get("project_id") or "").strip()
        if not pid:
            continue
        name = (row.get("display_name") or "").strip() or pid
        out[pid] = name
    return out
```

- [ ] **Step 4: Run the tests — verify they pass**

Run: `cd platform/lambda/shasta_runner_gcp && uv run --with pytest python -m pytest app/tests/test_project_discovery.py -v`
Expected: PASS — 5 prior tests + 4 new = 9 passed.

- [ ] **Step 5: Run the full scanner suite**

Run: `cd platform/lambda/shasta_runner_gcp && uv run --with pytest python -m pytest app/tests/ -q`
Expected: 38 passed (the prior 34 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/shasta_runner_gcp/app/project_discovery.py \
        platform/lambda/shasta_runner_gcp/app/tests/test_project_discovery.py
git commit -m "feat: project_discovery.enumerate_projects for org-mode scans"
```

---

### Task 4: `shasta_runner_gcp/main.py` — Stage 1 org-mode enumeration

**File:** `platform/lambda/shasta_runner_gcp/app/main.py`

In org mode the scanner enumerates projects via `enumerate_projects` before the footprint probe, writes the result back to `cloud_connections.scope.projects`, and uses the enumerated list as `project_ids` if the trigger didn't supply one. This mirrors the Azure scanner's `_record_subscription_names`.

- [ ] **Step 1: Add the import for `enumerate_projects` and an rds-data client**

Find the existing adapter-module import block (around lines 41-45):

```python
# === Adapter modules (this package) ===
from gcp_credential    import (build_external_account_info,
                               export_aws_credentials_to_env)
from gcp_findings      import convert_gcp_findings, project_entity
from gcp_units         import modules_for_tier
from project_discovery import discover_projects
```

Replace it with (add `enumerate_projects` to the project_discovery import):

```python
# === Adapter modules (this package) ===
from gcp_credential    import (build_external_account_info,
                               export_aws_credentials_to_env)
from gcp_findings      import convert_gcp_findings, project_entity
from gcp_units         import modules_for_tier
from project_discovery import discover_projects, enumerate_projects
```

Then find where the `update_scan` import is (around line 51):

```python
from scan_state     import record_scan_scope, update_scan
```

Immediately after the line `rds = boto3.client("rds-data")` is NOT yet present in this file — confirm by `grep -n "rds-data" /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_gcp/app/main.py`. If absent, add it. Find the `_SCANNER_VERSION = ...` line (around line 55) and immediately BEFORE it, insert:

```python
# An rds-data client for writing the enumerated project list back to
# cloud_connections.scope in org mode. boto3.client is offline so
# constructing it at module load is safe.
import json as _json
_rds = boto3.client("rds-data")

DB_CLUSTER_ARN = os.environ.get("DB_CLUSTER_ARN", "")
DB_SECRET_ARN  = os.environ.get("DB_SECRET_ARN",  "")
DB_NAME        = os.environ.get("DB_NAME",        "ciso_copilot")
```

(`os` was removed from the imports during Slice 1a review-fixes. Restore the `import os` at the top with the other stdlib imports — find `import traceback` and add `import os` on the line above it.)

- [ ] **Step 2: Add the `_record_projects` helper**

At the very END of `main.py`, append:

```python


def _record_projects(conn_id: str, projects: dict[str, str]) -> None:
    """Persist {project_id: display_name} into the connection's scope so
    the (future) project picker can show readable names. Additive —
    jsonb_set leaves the other scope keys untouched. Best-effort: a
    write failure must never fail the scan."""
    if not projects:
        return
    try:
        _rds.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=("UPDATE cloud_connections "
                 "SET scope = jsonb_set(COALESCE(scope, '{}'::jsonb), "
                 "                      '{projects}', CAST(:projects AS JSONB)) "
                 "WHERE conn_id = CAST(:cid AS UUID)"),
            parameters=[
                {"name": "cid",      "value": {"stringValue": conn_id}},
                {"name": "projects", "value": {"stringValue": _json.dumps(projects)}},
            ],
        )
    except Exception as e:
        print(f"WARN: project-name capture failed: {e}")
```

- [ ] **Step 3: Read `mode` from the event and call `enumerate_projects` in org mode**

Find the handler block that extracts event fields (around line 92-99):

```python
def handler(event: dict, context) -> dict:
    scan_id            = event["scan_id"]
    tenant_id          = event["tenant_id"]
    conn_id            = event["conn_id"]
    project_ids        = event["project_ids"]
    wif_project_number = event["wif_project_number"]
    sa_email           = event["sa_email"]
    wif_pool           = event["wif_pool"]
    wif_provider       = event["wif_provider"]
    scan_tier          = event.get("scan_tier", "quick")
```

Replace with (add `mode` and allow `project_ids` to be empty for org mode):

```python
def handler(event: dict, context) -> dict:
    scan_id            = event["scan_id"]
    tenant_id          = event["tenant_id"]
    conn_id            = event["conn_id"]
    project_ids        = list(event.get("project_ids") or [])
    wif_project_number = event["wif_project_number"]
    sa_email           = event["sa_email"]
    wif_pool           = event["wif_pool"]
    wif_provider       = event["wif_provider"]
    scan_tier          = event.get("scan_tier", "quick")
    mode               = (event.get("mode") or "project").lower()
```

- [ ] **Step 4: Wire enumeration into Stage 1 (org mode only)**

Find this block (around lines 109-117, just after the WIF credential is built and `base_client` is created):

```python
        # A base client bound to the first project — used only to mint
        # per-project sibling clients via for_project().
        base_client = GCPClient(project_id=project_ids[0],
                                credentials=credentials)

        # --- Stage 1 + 2: project discovery ----------------------------
        def _probe(project_id: str) -> str:
            c = base_client.for_project(project_id)
            c.validate_credentials()             # raises if unreachable
            return "active" if c.discover_services() else "empty"
```

Replace with (org-mode branch enumerates and refreshes `scope.projects`; the base client needs a fallback project for `list_projects` when `project_ids` is empty):

```python
        # In org mode we may have no project_ids yet (the webhook stores
        # an empty list; the user has not picked a subset). Use the
        # host_project_id (passed via wif_project_number's sibling
        # `host_project_id` field, or recovered from the audience) as a
        # bootstrap project so list_projects() has *some* project to
        # bind the GCPClient to. In project mode project_ids is the
        # single onboarded project.
        if mode == "org" and not project_ids:
            bootstrap_project = event.get("host_project_id") or wif_project_number
        else:
            bootstrap_project = project_ids[0]
        base_client = GCPClient(project_id=bootstrap_project,
                                credentials=credentials)

        # Org mode: enumerate every project the SA can see and write the
        # list back to the connection. Then, if the trigger didn't pass
        # a chosen subset, scan everything; if it did, honour the
        # subset.
        if mode == "org":
            discovered = enumerate_projects(base_client)
            print(f"org-mode enumeration: {len(discovered)} projects")
            _record_projects(conn_id, discovered)
            if not project_ids:
                project_ids = list(discovered.keys())

        if not project_ids:
            raise RuntimeError("no projects to scan (empty project_ids "
                               "and org enumeration returned nothing)")

        # --- Stage 1 + 2: project discovery ----------------------------
        def _probe(project_id: str) -> str:
            c = base_client.for_project(project_id)
            c.validate_credentials()             # raises if unreachable
            return "active" if c.discover_services() else "empty"
```

- [ ] **Step 5: Verify the module parses**

Run: `cd platform/lambda/shasta_runner_gcp && uv run python -c "import ast; ast.parse(open('app/main.py').read()); print('parses OK')"`
Expected: `parses OK`.

- [ ] **Step 6: Run the full scanner suite (main.py is not unit-tested, but the suite must still pass)**

Run: `cd platform/lambda/shasta_runner_gcp && uv run --with pytest python -m pytest app/tests/ -q`
Expected: 38 passed.

- [ ] **Step 7: Commit**

```bash
git add platform/lambda/shasta_runner_gcp/app/main.py
git commit -m "feat: gcp scanner enumerates projects in org mode (writes scope.projects)"
```

---

### Task 5: `run.py` — `MODE` env var and optional `PROJECT_IDS`

**File:** `platform/lambda/shasta_runner_gcp/app/run.py`
**Test file:** `platform/lambda/shasta_runner_gcp/app/tests/test_run.py`

The Fargate entrypoint must accept `MODE` (default `"project"`) and allow `PROJECT_IDS` + `WIF_PROJECT_NUMBER` to be empty in org mode (the scanner enumerates and the audience uses `WIF_PROJECT_NUMBER` which is the host project number — always passed). Add an optional `HOST_PROJECT_ID` for the org-mode bootstrap.

- [ ] **Step 1: Add the failing tests**

Open `platform/lambda/shasta_runner_gcp/app/tests/test_run.py` and append:

```python


def test_build_event_defaults_mode_to_project():
    assert build_event(_env())["mode"] == "project"


def test_build_event_respects_mode_env():
    env = _env(MODE="org")
    assert build_event(env)["mode"] == "org"


def test_build_event_allows_empty_project_ids_in_org_mode():
    env = _env(MODE="org", PROJECT_IDS="", HOST_PROJECT_ID="host-proj")
    event = build_event(env)
    assert event["project_ids"] == []
    assert event["host_project_id"] == "host-proj"


def test_build_event_passes_host_project_id_through():
    env = _env(MODE="org", HOST_PROJECT_ID="my-host")
    assert build_event(env)["host_project_id"] == "my-host"


def test_build_event_omits_host_project_id_when_unset():
    event = build_event(_env())
    assert "host_project_id" not in event or event["host_project_id"] is None
```

- [ ] **Step 2: Run the new tests — they should fail**

Run: `cd platform/lambda/shasta_runner_gcp && uv run --with pytest python -m pytest app/tests/test_run.py -v`
Expected: 5 prior tests pass, 5 new tests fail (`mode` key absent; `PROJECT_IDS=""` currently raises in the existing build_event because `_REQUIRED` includes it).

- [ ] **Step 3: Update `run.py`**

Replace the ENTIRE current contents of `platform/lambda/shasta_runner_gcp/app/run.py` with:

```python
"""Fargate entrypoint for the GCP scanner.

As a Lambda the scanner is invoked as main.handler(event, context). As a
Fargate task there is no event — scan parameters arrive as environment
variables (set via ecs:RunTask container overrides). This script reads
them into the event shape and calls the handler.

Usage (the container command for Fargate): python run.py

`from main import handler` is deferred into main() — main.py's
module-level code constructs boto3 clients and (when first used) imports
shasta.*, so importing it unconditionally would break build_event's test
collection. build_event is a pure function and stays independently
testable.

Env vars:
  Required (both modes):
    SCAN_ID, TENANT_ID, CONN_ID, SA_EMAIL, WIF_POOL, WIF_PROVIDER,
    WIF_PROJECT_NUMBER (the project hosting the WIF pool)
  Project mode:
    PROJECT_IDS (the single onboarded project, repeated for backward
                  compatibility — comma-separated string accepted)
  Org mode:
    MODE=org
    HOST_PROJECT_ID (the host project — used as a bootstrap for the
                     base GCPClient before enumeration runs; same value
                     as the project whose number is WIF_PROJECT_NUMBER)
    PROJECT_IDS may be empty (the scanner enumerates and uses the
                              result; or the trigger may pass a chosen
                              subset)
"""
from __future__ import annotations

import os
import sys

_REQUIRED = ("SCAN_ID", "TENANT_ID", "CONN_ID",
             "WIF_PROJECT_NUMBER", "SA_EMAIL", "WIF_POOL", "WIF_PROVIDER")


def build_event(env: dict[str, str]) -> dict:
    """Map scanner env vars to the event dict main.handler expects.
    PROJECT_IDS is a comma-separated list and may be empty in org mode.
    Raises KeyError if any of the always-required vars is missing."""
    event = {
        "scan_id":            env["SCAN_ID"],
        "tenant_id":          env["TENANT_ID"],
        "conn_id":            env["CONN_ID"],
        "project_ids":        [p.strip() for p in env.get("PROJECT_IDS", "").split(",")
                               if p.strip()],
        "wif_project_number": env["WIF_PROJECT_NUMBER"],
        "sa_email":           env["SA_EMAIL"],
        "wif_pool":           env["WIF_POOL"],
        "wif_provider":       env["WIF_PROVIDER"],
        "scan_tier":          env.get("SCAN_TIER", "quick"),
        "mode":               (env.get("MODE") or "project").lower(),
    }
    host = env.get("HOST_PROJECT_ID")
    if host:
        event["host_project_id"] = host
    return event


def main() -> None:
    missing = [v for v in _REQUIRED if not os.environ.get(v)]
    if missing:
        print(f"FATAL: missing required env vars: {missing}")
        sys.exit(1)
    from main import handler  # deferred — avoids module-level imports at collection
    result = handler(build_event(dict(os.environ)), None)
    print(f"scan finished: {result}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests — verify they pass**

Run: `cd platform/lambda/shasta_runner_gcp && uv run --with pytest python -m pytest app/tests/test_run.py -v`
Expected: 10 passed (5 prior + 5 new).

- [ ] **Step 5: Run the full scanner suite**

Run: `cd platform/lambda/shasta_runner_gcp && uv run --with pytest python -m pytest app/tests/ -q`
Expected: 43 passed (38 + 5 new).

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/shasta_runner_gcp/app/run.py \
        platform/lambda/shasta_runner_gcp/app/tests/test_run.py
git commit -m "feat: run.py supports MODE=org and empty PROJECT_IDS"
```

---

### Task 6: `connections_list._rescan_gcp` — route on `scope.mode`

**File:** `platform/lambda/connections_list/main.py`

The rescan trigger must read `scope.mode`. In project mode (or when `mode` is absent — legacy connections): pass the single project. In org mode: pass `MODE=org`, `HOST_PROJECT_ID`, and the chosen-subset list (which may be empty on first scan).

- [ ] **Step 1: Rewrite `_rescan_gcp`**

Find the entire `_rescan_gcp` function (the version installed by Slice 1b; from `def _rescan_gcp(` through its closing `return scan_id`) and replace it with:

```python
def _rescan_gcp(conn: dict, tenant_id: str, tier: str) -> str:
    """Start one v2 GCP Fargate scan at `tier`. Routes on scope.mode —
    `project` (single-project onboarding, default) or `org` (multi-
    project onboarding from Slice 2a)."""
    if not (GCP_SCAN_TASK_DEF and SCAN_CLUSTER_ARN and SCAN_SUBNET_IDS):
        raise _IncompleteConnection("gcp scan task not configured")

    scope = conn.get("scope") or {}
    mode  = (scope.get("mode") or "project").lower()

    if mode == "org":
        required = ("host_project_number", "sa_email", "wif_pool",
                    "wif_provider")
        missing = [k for k in required if not scope.get(k)]
        if missing:
            raise _IncompleteConnection(f"missing scope fields: {','.join(missing)}")
        wif_project_number = scope["host_project_number"]
        host_project_id    = scope.get("host_project_id", "")
        # On first scan after onboarding, selected may be empty — the
        # scanner enumerates. On subsequent scans, honour the user's
        # picked subset.
        project_ids        = scope.get("selected") or []
    elif mode == "project":
        required = ("project_id", "project_number", "sa_email",
                    "wif_pool", "wif_provider")
        missing = [k for k in required if not scope.get(k)]
        if missing:
            raise _IncompleteConnection(f"missing scope fields: {','.join(missing)}")
        wif_project_number = scope["project_number"]
        host_project_id    = scope["project_id"]
        project_ids        = [scope["project_id"]]
    else:
        raise _IncompleteConnection(f"unknown scope.mode: {mode}")

    scan_id = str(uuid.uuid4())
    _insert_scan(scan_id, tenant_id, conn["conn_id"], {}, tier=tier)
    try:
        ecs.run_task(
            cluster=SCAN_CLUSTER_ARN,
            taskDefinition=GCP_SCAN_TASK_DEF,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets":        [s for s in SCAN_SUBNET_IDS.split(",") if s],
                    "securityGroups": [SCAN_SECURITY_GROUP_ID] if SCAN_SECURITY_GROUP_ID else [],
                    "assignPublicIp": "DISABLED",
                },
            },
            overrides={
                "containerOverrides": [{
                    "name": "scanner",
                    "environment": [
                        {"name": "SCAN_ID",            "value": scan_id},
                        {"name": "TENANT_ID",          "value": tenant_id},
                        {"name": "CONN_ID",            "value": conn["conn_id"]},
                        {"name": "MODE",               "value": mode},
                        {"name": "PROJECT_IDS",        "value": ",".join(project_ids)},
                        {"name": "HOST_PROJECT_ID",    "value": host_project_id},
                        {"name": "WIF_PROJECT_NUMBER", "value": wif_project_number},
                        {"name": "SA_EMAIL",           "value": scope["sa_email"]},
                        {"name": "WIF_POOL",           "value": scope["wif_pool"]},
                        {"name": "WIF_PROVIDER",       "value": scope["wif_provider"]},
                        {"name": "SCAN_TIER",          "value": tier},
                    ],
                }],
            },
        )
        print(f"gcp rescan {scan_id} ({tier}, mode={mode}) started for {conn['conn_id']}")
    except Exception as e:
        print(f"WARN: gcp rescan RunTask failed for {conn['conn_id']}: {e}")
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql="UPDATE scans SET status='failed' WHERE scan_id = CAST(:sid AS UUID)",
            parameters=[{"name": "sid", "value": {"stringValue": scan_id}}],
        )
    return scan_id
```

- [ ] **Step 2: Verify the module parses**

Run: `cd platform/lambda/connections_list && python3 -c "import ast; ast.parse(open('main.py').read()); print('parses OK')"`
Expected: `parses OK`.

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/connections_list/main.py
git commit -m "feat: _rescan_gcp routes on scope.mode (project vs org)"
```

---

### Task 7: Build the scanner image and deploy CisoCopilotScan

Slice 2a touches scanner code (Tasks 3-5); the Lambda code (Tasks 2, 6) is picked up on next CDK deploy without rebuild because the API stack's `lambda.Code.fromAsset` re-hashes the directory.

- [ ] **Step 1: Rebuild + push the GCP scanner image**

Run: `cd platform/lambda/shasta_runner_gcp && ./build.sh`
Expected: ends with `==> done. Image URI: ...dkr.ecr.us-east-1.amazonaws.com/shasta-runner-gcp:latest`.

- [ ] **Step 2: Deploy CisoCopilotApi (picks up the Lambda code changes)**

Run: `cd platform && npx cdk deploy CisoCopilotApi --exclusively --require-approval never`
Expected: `UPDATE_COMPLETE`.

CisoCopilotScan doesn't need a new deploy — the task def pins the `:latest` tag, and the next `RunTask` pulls the new image. (Confirm by checking the Slice 1b HANDOFF entry: the Slice-1a build noted "task def pins the `:latest` tag, which CDK does not diff".)

- [ ] **Step 3: Confirm the deployed Lambdas reflect the changes**

```bash
# onboarding_gcp_complete should now branch on mode — quick smoke via a
# 400 from a stub event (no external_id, but mode=org → missing_fields
# OR mode invalid → invalid_mode).
aws lambda invoke --function-name "$(aws lambda list-functions \
  --query "Functions[?contains(FunctionName,'OnboardingGcpComplete')].FunctionName" \
  --output text)" \
  --payload "$(printf '%s' '{"body":"{\"mode\":\"org\"}"}' | base64)" \
  /tmp/gcp-onboard-smoke.json >/dev/null && cat /tmp/gcp-onboard-smoke.json && echo
```

Expected: a 400 with `{"error":"missing_fields"}` — confirming the deployed Lambda is the new mode-branching version.

- [ ] **Step 4: Commit (nothing to add — this task is build/deploy only)**

No commit needed.

---

### Task 8: Human-gated live verification (KK runs the org `onboard.sh`)

This task cannot be executed by an agent — it requires real org-admin on a GCP Organization. Document the procedure so KK can run it whenever the access is available.

- [ ] **Step 1: Update HANDOFF.md with the verification procedure**

In `HANDOFF.md`, in the GCP-uplift section, add a new sub-section:

```markdown
**Slice 2a live-verification — pending (human-gated).** Requires org-admin
on a real GCP Organization. Procedure when ready:

1. In Cloud Shell of the customer's host project (org-admin signed in):
   ```bash
   curl -fsSL https://cdn.settlingforless.com/gcp/onboard.sh \
     | bash -s -- <EXTERNAL_ID> --org <ORG_ID>
   ```
   `<EXTERNAL_ID>` comes from the web app's "Add GCP organisation" flow
   (which writes a pending row); `<ORG_ID>` is the numeric organisation
   id (`gcloud organizations list`).
2. Verify the connection lands `active`, `scope.mode='org'`, `projects={}`,
   `selected=[]`:
   ```sql
   SELECT scope FROM cloud_connections
   WHERE external_id='<EXTERNAL_ID>';
   ```
3. From the Connect page, click rescan on the new GCP row (or invoke
   ConnectionsListFn directly with the synthetic event from the Slice 1b
   verification, substituting the new conn_id).
4. Watch the scan progress through `region_discovery → first_signal →
   crown_jewel → done`. The first scan does the enumeration: confirm
   `scope.projects` is now populated with `{pid: name}` for every project
   under the org, and `selected` was filled in by the scanner with the
   discovered list (verify by re-querying `cloud_connections.scope`).
5. Confirm findings landed: `SELECT count(*) FROM findings WHERE
   scan_id=...`.
```

- [ ] **Step 2: Commit**

```bash
git add HANDOFF.md
git commit -m "docs: record GCP scanner uplift Slice 2a shipped"
```

---

## Self-review

**Spec coverage** (against `2026-05-22-gcp-scanner-uplift-design.md` §6):
- §6.1 WIF host-project constraint — Task 1 keeps the WIF pool in the host project.
- §6.2 new org variant of `onboard.sh` — Task 1.
- §6.3 webhook branches on `mode` — Task 2. **Deviation:** the spec called for the webhook to enumerate projects via Resource Manager (Approach C, hybrid). This plan defers that to scan-time (Approach B) to avoid bundling google-auth + IAM-trust changes into the webhook for tonight; documented explicitly in the Background section.

**Placeholder scan:** the `<EXTERNAL_ID>` / `<ORG_ID>` tokens in Task 8 are runtime values KK fills in; everything else is concrete. No "TBD"/"implement later" anywhere.

**Type / contract consistency:** the env-var names — `MODE`, `PROJECT_IDS`, `HOST_PROJECT_ID`, `WIF_PROJECT_NUMBER`, `SA_EMAIL`, `WIF_POOL`, `WIF_PROVIDER`, `SCAN_TIER`, `SCAN_ID`, `TENANT_ID`, `CONN_ID` — match between Task 5 (`run.py`/`build_event`) and Task 6 (`_rescan_gcp` `ecs.run_task` overrides). The event-key names in `run.py`'s `build_event` output (`mode`, `project_ids`, `host_project_id`, `wif_project_number`, `sa_email`, `wif_pool`, `wif_provider`, `scan_tier`, `scan_id`, `tenant_id`, `conn_id`) match the keys read by `main.py`'s `handler` (Task 4). The `scope.mode` / `scope.host_project_number` / `scope.selected` / `scope.projects` field names match between Task 2 (webhook write) and Task 6 (rescan read).

**Scope cuts vs spec — explicit:**
- Webhook does NOT enumerate via Resource Manager. Picker (Slice 2b) is empty until the first scan completes.
- Org mode does NOT auto-scan on onboarding (the user manually starts the first scan). Project mode keeps the historical auto-scan-on-onboard behaviour.

Both deviations are documented in Task 2 + Background, and both can be revisited in a follow-up slice without further architectural change.

No issues found.
