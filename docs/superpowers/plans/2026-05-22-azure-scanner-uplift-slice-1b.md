# Azure Scanner Uplift — Slice 1b: Production Trigger Rewiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reach the Azure v2 scanner (Slice 1a) from the real product flows — switch `onboarding_azure_complete` and the `_rescan_azure` path from `lambda.invoke` of the legacy single-pass Azure Lambda to `ecs:RunTask` of the `ciso-copilot-azure-scan` Fargate task — and retire the legacy Azure Lambda.

**Architecture:** Both Azure triggers currently fire one `lambda.invoke` per subscription at the legacy `shasta-runner-azure` Lambda. Slice 1a built a v2 Fargate scanner that scans all selected subscriptions of a connection in one task run. Slice 1b makes each trigger start **one** Fargate task (all subscriptions, one `scans` row), mirroring how `_rescan_aws` already works. The legacy Azure `DockerImageFunction` and its cross-stack wiring are then removed.

**Tech Stack:** Python 3.12, boto3 (`ecs`, `rds-data`, `secretsmanager`), AWS CDK (TypeScript), ECS Fargate.

**Spec:** `docs/superpowers/specs/2026-05-21-azure-scanner-uplift-design.md` (§7). Slice 1a plan: `docs/superpowers/plans/2026-05-22-azure-scanner-uplift-slice-1a.md`.

---

## Background — current state (verified)

- **`platform/lambda/onboarding_azure_complete/main.py`** — `_enqueue_initial_scan` inserts one `scans` row per subscription and `lambda_client.invoke`s the legacy Azure runner (`AZURE_RUNNER_FN`) once per subscription.
- **`platform/lambda/connections_list/main.py`** — `_rescan_azure(conn, tenant_id)` loops `scope.subscriptions`, inserting one `scans` row + one `_invoke_async(AZURE_RUNNER_FN, …)` per subscription. `_rescan_aws(conn, tenant_id, tier)` is the **template** to mirror: it inserts one row via `_insert_scan(scan_id, tenant_id, conn_id, {}, tier=tier)` and does one `ecs.run_task` with container overrides. `_rescan` (the dispatcher) reads `tier` from the request body (default `medium`, validated to `quick`/`medium`) and calls `_rescan_azure(conn, tenant_id)` — note: **no tier passed today**.
- **`platform/lib/api-stack.ts`** — `connectionsListFn` already has `SCAN_CLUSTER_ARN` / `SCAN_TASK_DEF_ARN` / `SCAN_SUBNET_IDS` / `SCAN_SECURITY_GROUP_ID` env vars and the AWS `ecs:RunTask` + `iam:PassRole` policy. `onboardingAzureCompleteFn` has only `AZURE_RUNNER_FN`. Both have `AZURE_RUNNER_FN` + `props.shastaRunnerAzure.grantInvoke(...)`.
- **`platform/lib/scan-stack.ts`** — the legacy Azure Lambda is the `AzureRunner` `DockerImageFunction`, exposed as the public field `shastaRunnerAzure`. Slice 1a added the `azureScanTaskDef` Fargate task def (family `ciso-copilot-azure-scan`) as a public field.
- **`platform/bin/platform.ts`** — wires `scanStack.shastaRunnerAzure` into `ApiStack`'s `shastaRunnerAzure` prop; passes `scanTaskDefFamily: 'ciso-copilot-aws-scan'` (a hardcoded literal — avoids a cross-stack export on a revisioned ARN) and `scanTaskDefTaskRoleArn`/`scanTaskDefExecutionRoleArn` read off `scanStack.scanTaskDef`.
- The Azure scan **cluster, subnets, and security group are shared** with the AWS scanner — only the task definition differs (`ciso-copilot-azure-scan`).

---

## Conventions

- Work on a branch: before Task 1, `git checkout -b feat/azure-scanner-slice-1b` from `main`. Commit after each task. Never `--no-verify`.
- Lambda Python here has no local test venv and no unit tests; verification is `python3 -m py_compile`, `npx cdk synth`, and the live E2E in Task 6 — consistent with how `connections_list` / `onboarding_azure_complete` are already maintained.
- New env var name used throughout: **`AZURE_SCAN_TASK_DEF`** — the Azure Fargate task-def family (`ciso-copilot-azure-scan`).

---

## Task 1: `onboarding_azure_complete` — one Fargate task per connection

Replace the per-subscription `lambda.invoke` with a single `ecs.run_task` that scans every subscription in one run and inserts one `scans` row.

**Files:**
- Modify: `platform/lambda/onboarding_azure_complete/main.py`

- [ ] **Step 1: Swap the module-level clients and config**

In `main.py`, the current block reads:

```python
DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
AZURE_RUNNER_FN = os.environ.get("AZURE_RUNNER_FN", "")

rds_data       = boto3.client("rds-data")
sm             = boto3.client("secretsmanager")
lambda_client  = boto3.client("lambda")
```

Replace it with:

```python
DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

# v2 Azure scanner — one Fargate task scans all subscriptions of a
# connection. Cluster / subnets / security group are shared with the
# AWS scanner; only the task definition differs.
AZURE_SCAN_TASK_DEF    = os.environ.get("AZURE_SCAN_TASK_DEF", "")
SCAN_CLUSTER_ARN       = os.environ.get("SCAN_CLUSTER_ARN", "")
SCAN_SUBNET_IDS        = os.environ.get("SCAN_SUBNET_IDS", "")
SCAN_SECURITY_GROUP_ID = os.environ.get("SCAN_SECURITY_GROUP_ID", "")

rds_data = boto3.client("rds-data")
sm       = boto3.client("secretsmanager")
ecs      = boto3.client("ecs")
```

- [ ] **Step 2: Replace the scan-enqueue call in `handler`**

In `handler`, the current block reads:

```python
    # Kick an initial scan per subscription. Each scan is independent so the
    # scanner code stays single-sub; the connection's full posture is the union
    # of all scans tied to it.
    scan_ids = [
        _enqueue_initial_scan(
            tenant_id        = conn["tenant_id"],
            conn_id          = conn["conn_id"],
            azure_tenant_id  = azure_tenant_id,
            client_id        = client_id,
            secret_arn       = secret_arn,
            subscription_id  = sub_id,
        )
        for sub_id in subscription_ids
    ]
    scan_ids = [s for s in scan_ids if s]

    return _resp(200, {
        "status":              "active",
        "connection_id":       conn["conn_id"],
        "subscriptions_count": len(subscription_ids),
        "initial_scan_ids":    scan_ids,
    })
```

Replace it with:

```python
    # Kick one initial scan for the whole connection — the v2 Fargate
    # scanner walks every selected subscription in a single task run.
    scan_id = _run_initial_scan(
        tenant_id        = conn["tenant_id"],
        conn_id          = conn["conn_id"],
        azure_tenant_id  = azure_tenant_id,
        client_id        = client_id,
        secret_arn       = secret_arn,
        subscription_ids = subscription_ids,
    )

    return _resp(200, {
        "status":              "active",
        "connection_id":       conn["conn_id"],
        "subscriptions_count": len(subscription_ids),
        "initial_scan_ids":    [scan_id] if scan_id else [],
    })
```

- [ ] **Step 3: Replace `_enqueue_initial_scan` with `_run_initial_scan`**

Delete the entire `_enqueue_initial_scan` function and replace it with:

```python
def _run_initial_scan(*, tenant_id: str, conn_id: str, azure_tenant_id: str,
                      client_id: str, secret_arn: str,
                      subscription_ids: list[str]) -> str | None:
    """Insert one `scans` row and start one v2 Azure Fargate task that
    scans every subscription of the connection."""
    if not (AZURE_SCAN_TASK_DEF and SCAN_CLUSTER_ARN and SCAN_SUBNET_IDS):
        print("WARN: azure scan task not configured; skipping initial scan")
        return None

    scan_id = str(uuid.uuid4())
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "INSERT INTO scans (scan_id, tenant_id, conn_id, trigger, "
            "                   status, tier, phase) "
            "VALUES (CAST(:sid AS UUID), CAST(:tid AS UUID), "
            "        CAST(:cid AS UUID), 'onboarding', 'queued', "
            "        'quick', 'region_discovery')"
        ),
        parameters=[
            {"name": "sid", "value": {"stringValue": scan_id}},
            {"name": "tid", "value": {"stringValue": tenant_id}},
            {"name": "cid", "value": {"stringValue": conn_id}},
        ],
    )

    try:
        ecs.run_task(
            cluster=SCAN_CLUSTER_ARN,
            taskDefinition=AZURE_SCAN_TASK_DEF,
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
                        {"name": "SCAN_ID",          "value": scan_id},
                        {"name": "TENANT_ID",        "value": tenant_id},
                        {"name": "CONN_ID",          "value": conn_id},
                        {"name": "AZURE_TENANT_ID",  "value": azure_tenant_id},
                        {"name": "CLIENT_ID",        "value": client_id},
                        {"name": "SECRET_ARN",       "value": secret_arn},
                        {"name": "SUBSCRIPTION_IDS", "value": ",".join(subscription_ids)},
                        {"name": "SCAN_TIER",        "value": "quick"},
                    ],
                }],
            },
        )
        print(f"azure onboarding scan {scan_id} started for {conn_id}")
    except Exception as e:
        print(f"WARN: azure scan RunTask failed for {conn_id}: {e}")
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql="UPDATE scans SET status='failed' WHERE scan_id = CAST(:sid AS UUID)",
            parameters=[{"name": "sid", "value": {"stringValue": scan_id}}],
        )

    return scan_id
```

- [ ] **Step 4: Syntax-check**

Run:
```bash
cd platform/lambda/onboarding_azure_complete && python3 -m py_compile main.py && echo "py_compile OK"
```
Expected: `py_compile OK`.

- [ ] **Step 5: Confirm no stale legacy reference remains**

Run:
```bash
cd platform/lambda/onboarding_azure_complete && \
  grep -n "AZURE_RUNNER_FN\|lambda_client\|_enqueue_initial_scan" main.py
```
Expected: **no output**.

- [ ] **Step 6: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/onboarding_azure_complete/main.py
git commit -m "$(cat <<'EOF'
feat: onboarding_azure_complete starts one v2 Fargate scan

Replaces the per-subscription legacy Lambda invoke with a single
ecs:RunTask of ciso-copilot-azure-scan — one task, one scans row, all
subscriptions.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `connections_list` — `_rescan_azure` to Fargate, tier-aware

Rewrite `_rescan_azure` to start one Fargate task (mirroring `_rescan_aws`), and make it tier-aware.

**Files:**
- Modify: `platform/lambda/connections_list/main.py`

- [ ] **Step 1: Add the `AZURE_SCAN_TASK_DEF` env var**

In `main.py`, the config block currently reads:

```python
AZURE_RUNNER_FN  = os.environ.get("AZURE_RUNNER_FN", "")
ENTRA_RUNNER_FN  = os.environ.get("ENTRA_RUNNER_FN", "")
GCP_RUNNER_FN    = os.environ.get("GCP_RUNNER_FN", "")
SCAN_CLUSTER_ARN       = os.environ.get("SCAN_CLUSTER_ARN", "")
SCAN_TASK_DEF_ARN      = os.environ.get("SCAN_TASK_DEF_ARN", "")
SCAN_SUBNET_IDS        = os.environ.get("SCAN_SUBNET_IDS", "")
SCAN_SECURITY_GROUP_ID = os.environ.get("SCAN_SECURITY_GROUP_ID", "")
```

Replace it with (drops `AZURE_RUNNER_FN`; `ENTRA_RUNNER_FN`/`GCP_RUNNER_FN` stay — those scanners are still Lambdas — adds `AZURE_SCAN_TASK_DEF`):

```python
ENTRA_RUNNER_FN  = os.environ.get("ENTRA_RUNNER_FN", "")
GCP_RUNNER_FN    = os.environ.get("GCP_RUNNER_FN", "")
SCAN_CLUSTER_ARN       = os.environ.get("SCAN_CLUSTER_ARN", "")
SCAN_TASK_DEF_ARN      = os.environ.get("SCAN_TASK_DEF_ARN", "")
AZURE_SCAN_TASK_DEF    = os.environ.get("AZURE_SCAN_TASK_DEF", "")
SCAN_SUBNET_IDS        = os.environ.get("SCAN_SUBNET_IDS", "")
SCAN_SECURITY_GROUP_ID = os.environ.get("SCAN_SECURITY_GROUP_ID", "")
```

- [ ] **Step 2: Pass `tier` to `_rescan_azure` in the dispatcher**

In the `_rescan` function, the dispatch block currently reads:

```python
        if cloud == "aws":
            scan_id = _rescan_aws(conn, tenant_id, tier)
        elif cloud == "azure":
            scan_id = _rescan_azure(conn, tenant_id)
        elif cloud == "entra":
```

Change the `azure` line to pass `tier`:

```python
        if cloud == "aws":
            scan_id = _rescan_aws(conn, tenant_id, tier)
        elif cloud == "azure":
            scan_id = _rescan_azure(conn, tenant_id, tier)
        elif cloud == "entra":
```

- [ ] **Step 3: Rewrite `_rescan_azure`**

Replace the entire `_rescan_azure` function with:

```python
def _rescan_azure(conn: dict, tenant_id: str, tier: str) -> str:
    """Start one v2 Azure Fargate scan at `tier` — one task scans every
    subscription in the connection's scope. Mirrors _rescan_aws."""
    if not (AZURE_SCAN_TASK_DEF and SCAN_CLUSTER_ARN and SCAN_SUBNET_IDS):
        raise _IncompleteConnection("azure scan task not configured")
    secret_arn = conn.get("credentials_secret_arn")
    if not secret_arn:
        raise _IncompleteConnection("missing credentials_secret_arn")

    secret = _get_secret_json(secret_arn)
    azure_tenant_id = secret.get("azure_tenant_id")
    client_id       = secret.get("client_id")
    if not azure_tenant_id or not client_id:
        raise _IncompleteConnection("missing azure_tenant_id or client_id in secret")

    scope = conn.get("scope") or {}
    subscriptions = scope.get("subscriptions") or []
    if not subscriptions:
        raise _IncompleteConnection("missing subscriptions in scope")

    scan_id = str(uuid.uuid4())
    _insert_scan(scan_id, tenant_id, conn["conn_id"], {}, tier=tier)
    try:
        ecs.run_task(
            cluster=SCAN_CLUSTER_ARN,
            taskDefinition=AZURE_SCAN_TASK_DEF,
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
                        {"name": "SCAN_ID",          "value": scan_id},
                        {"name": "TENANT_ID",        "value": tenant_id},
                        {"name": "CONN_ID",          "value": conn["conn_id"]},
                        {"name": "AZURE_TENANT_ID",  "value": azure_tenant_id},
                        {"name": "CLIENT_ID",        "value": client_id},
                        {"name": "SECRET_ARN",       "value": secret_arn},
                        {"name": "SUBSCRIPTION_IDS", "value": ",".join(subscriptions)},
                        {"name": "SCAN_TIER",        "value": tier},
                    ],
                }],
            },
        )
        print(f"azure rescan {scan_id} ({tier}) started for {conn['conn_id']}")
    except Exception as e:
        print(f"WARN: azure rescan RunTask failed for {conn['conn_id']}: {e}")
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql="UPDATE scans SET status='failed' WHERE scan_id = CAST(:sid AS UUID)",
            parameters=[{"name": "sid", "value": {"stringValue": scan_id}}],
        )
    return scan_id
```

- [ ] **Step 4: Syntax-check**

Run:
```bash
cd platform/lambda/connections_list && python3 -m py_compile main.py && echo "py_compile OK"
```
Expected: `py_compile OK`.

- [ ] **Step 5: Confirm no stale Azure-Lambda reference remains**

Run:
```bash
cd platform/lambda/connections_list && grep -n "AZURE_RUNNER_FN" main.py
```
Expected: **no output**. (`_invoke_async` and `lambda_client` stay — `_rescan_entra` / `_rescan_gcp` still use them. Do not remove those.)

- [ ] **Step 6: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/connections_list/main.py
git commit -m "$(cat <<'EOF'
feat: _rescan_azure starts one tier-aware v2 Fargate scan

Replaces the per-subscription legacy Lambda invokes with one
ecs:RunTask of ciso-copilot-azure-scan, mirroring _rescan_aws. The
rescan dispatcher now passes the requested tier through.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: CDK — wire the Azure Fargate task into both Lambdas

Give `onboardingAzureCompleteFn` and `connectionsListFn` the `AZURE_SCAN_TASK_DEF` env var and the `ecs:RunTask` + `iam:PassRole` permissions for the Azure task definition. This task only **adds** wiring — the legacy Lambda removal is Task 4 — so `cdk synth` stays green throughout.

**Files:**
- Modify: `platform/lib/api-stack.ts`
- Modify: `platform/lib/scan-stack.ts` (only if the Azure task-def role ARNs are not already reachable — see Step 1)
- Modify: `platform/bin/platform.ts`

- [ ] **Step 1: Confirm how the Azure task-def role ARNs are reachable**

Slice 1a added `azureScanTaskDef` (an `ecs.FargateTaskDefinition`) as a public readonly field on `ScanStack`. `bin/platform.ts` already reads the AWS equivalents as `scanStack.scanTaskDef.taskRole.roleArn` and `scanStack.scanTaskDef.executionRole!.roleArn`. Confirm `ScanStack` exposes `azureScanTaskDef` publicly:
```bash
grep -n "azureScanTaskDef" platform/lib/scan-stack.ts
```
Expected: a `public readonly azureScanTaskDef` declaration and its assignment. If it is public, no `scan-stack.ts` change is needed in this task — `bin/platform.ts` reads the role ARNs directly off it (Step 3). If it is NOT public, make it `public readonly` and assign it, mirroring `scanTaskDef`.

- [ ] **Step 2: Add the three Azure props to `ApiStackProps`**

In `platform/lib/api-stack.ts`, find the `ApiStackProps` interface. Near the existing AWS scan-task props (`scanTaskDefFamily`, `scanTaskDefTaskRoleArn`, `scanTaskDefExecutionRoleArn`), add:

```typescript
  azureScanTaskDefFamily:           string;
  azureScanTaskDefTaskRoleArn:      string;
  azureScanTaskDefExecutionRoleArn: string;
```

- [ ] **Step 3: Pass the Azure props from `bin/platform.ts`**

In `platform/bin/platform.ts`, in the `new ApiStack(...)` props object, alongside the existing `scanTaskDefFamily` / `scanTaskDefTaskRoleArn` / `scanTaskDefExecutionRoleArn` lines, add:

```typescript
  azureScanTaskDefFamily:           'ciso-copilot-azure-scan',
  azureScanTaskDefTaskRoleArn:      scanStack.azureScanTaskDef.taskRole.roleArn,
  azureScanTaskDefExecutionRoleArn: scanStack.azureScanTaskDef.executionRole!.roleArn,
```

(The family name is a hardcoded literal — same pattern as `scanTaskDefFamily: 'ciso-copilot-aws-scan'` — to avoid a cross-stack export on a revisioned task-def ARN. Role ARNs are stable and safe to reference cross-stack.)

- [ ] **Step 4: Add the Azure env var + IAM to `onboardingAzureCompleteFn`**

In `api-stack.ts`, the `onboardingAzureCompleteFn` construct currently has:

```typescript
      environment: {
        ...dbEnv,
        AZURE_RUNNER_FN: props.shastaRunnerAzure.functionName,
      },
```

Replace that `environment` block with (drops `AZURE_RUNNER_FN`, adds the shared scan-infra vars + the Azure task def):

```typescript
      environment: {
        ...dbEnv,
        AZURE_SCAN_TASK_DEF:    props.azureScanTaskDefFamily,
        SCAN_CLUSTER_ARN:       props.scanCluster.clusterArn,
        SCAN_SUBNET_IDS:        props.vpc.privateSubnets.map(s => s.subnetId).join(','),
        SCAN_SECURITY_GROUP_ID: props.scanTaskSecurityGroupId,
      },
```

Then, immediately after the existing `onboardingAzureCompleteFn.addToRolePolicy(...)` for `secretsmanager:*` (and before the `props.shastaRunnerAzure.grantInvoke(onboardingAzureCompleteFn)` line, which Task 4 deletes), add the Azure `ecs:RunTask` + `iam:PassRole` policies:

```typescript
    onboardingAzureCompleteFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ecs:RunTask'],
      resources: [`arn:aws:ecs:${this.region}:${this.account}:task-definition/${props.azureScanTaskDefFamily}:*`],
    }));
    onboardingAzureCompleteFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['iam:PassRole'],
      resources: [
        props.azureScanTaskDefTaskRoleArn,
        props.azureScanTaskDefExecutionRoleArn,
      ],
    }));
```

- [ ] **Step 5: Add the Azure env var + IAM to `connectionsListFn`**

In `api-stack.ts`, the `connectionsListFn` `environment` block currently has `AZURE_RUNNER_FN`, `ENTRA_RUNNER_FN`, `GCP_RUNNER_FN`, `SCAN_CLUSTER_ARN`, `SCAN_TASK_DEF_ARN`, `SCAN_SUBNET_IDS`, `SCAN_SECURITY_GROUP_ID`. Change it so the `AZURE_RUNNER_FN` line is replaced by an `AZURE_SCAN_TASK_DEF` line:

```typescript
      environment: {
        ...dbEnv,
        AZURE_SCAN_TASK_DEF:    props.azureScanTaskDefFamily,
        ENTRA_RUNNER_FN:        props.shastaRunnerEntra.functionName,
        GCP_RUNNER_FN:          props.shastaRunnerGcp.functionName,
        SCAN_CLUSTER_ARN:       props.scanCluster.clusterArn,
        SCAN_TASK_DEF_ARN:      props.scanTaskDefFamily,
        SCAN_SUBNET_IDS:        props.vpc.privateSubnets.map(s => s.subnetId).join(','),
        SCAN_SECURITY_GROUP_ID: props.scanTaskSecurityGroupId,
      },
```

Then, immediately after the existing AWS `ecs:RunTask` policy statement on `connectionsListFn` (the one whose resource is `task-definition/${props.scanTaskDefFamily}:*`), add an Azure one:

```typescript
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ecs:RunTask'],
      resources: [`arn:aws:ecs:${this.region}:${this.account}:task-definition/${props.azureScanTaskDefFamily}:*`],
    }));
```

And extend the existing `connectionsListFn` `iam:PassRole` statement's `resources` array to also include the Azure roles. The statement currently reads:

```typescript
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['iam:PassRole'],
      resources: [
        props.scanTaskDefTaskRoleArn,
        props.scanTaskDefExecutionRoleArn,
      ],
    }));
```

Change its `resources` to:

```typescript
      resources: [
        props.scanTaskDefTaskRoleArn,
        props.scanTaskDefExecutionRoleArn,
        props.azureScanTaskDefTaskRoleArn,
        props.azureScanTaskDefExecutionRoleArn,
      ],
```

- [ ] **Step 6: Synth-check**

Run:
```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform && npx cdk synth CisoCopilotApi >/dev/null && echo "synth OK"
```
Expected: `synth OK`. (`props.shastaRunnerAzure` is still referenced — that is fine; Task 4 removes it. This task must keep synth green.)

- [ ] **Step 7: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lib/api-stack.ts platform/bin/platform.ts platform/lib/scan-stack.ts
git commit -m "$(cat <<'EOF'
feat: wire the Azure Fargate task def into the trigger Lambdas

onboarding_azure_complete + connections_list get AZURE_SCAN_TASK_DEF
plus ecs:RunTask / iam:PassRole for ciso-copilot-azure-scan. The legacy
Azure Lambda wiring is removed in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(If `scan-stack.ts` was not modified in Step 1, drop it from the `git add`.)

---

## Task 4: CDK — retire the legacy Azure Lambda

Remove the legacy Azure `DockerImageFunction`, its `ScanStack` public field, the `bin` wiring, the `ApiStackProps` prop, and the two `grantInvoke` calls. `cdk synth` must be green after this task.

**Files:**
- Modify: `platform/lib/scan-stack.ts`
- Modify: `platform/bin/platform.ts`
- Modify: `platform/lib/api-stack.ts`

- [ ] **Step 1: Confirm the legacy Lambda has no remaining consumers**

Run:
```bash
cd /Users/kkmookhey/Projects/CISOBrief && grep -rn "shastaRunnerAzure\b" platform/lib platform/bin
```
Expected consumers, all of which this task removes: the `AzureRunner` construct + `public readonly shastaRunnerAzure` in `scan-stack.ts`, the `shastaRunnerAzure` prop in `ApiStackProps` + its uses in `api-stack.ts`, and the `bin/platform.ts` wiring line. If `grep` shows a consumer NOT in that set, STOP and report it — do not remove a field something else needs. (Note: `shastaRunnerAzureRepo` — the ECR repo — is a DIFFERENT identifier; the Azure Fargate task def still uses it. Do not remove anything matching `shastaRunnerAzureRepo`.)

- [ ] **Step 2: Remove the `AzureRunner` construct from `scan-stack.ts`**

In `platform/lib/scan-stack.ts`, delete the `AzureRunner` `DockerImageFunction` construct (the legacy Azure Lambda), its IAM grants, and the `public readonly shastaRunnerAzure` field declaration plus the `this.shastaRunnerAzure = ...` assignment. Do NOT touch the `azureScanTaskDef` Fargate construct added in Slice 1a, and do NOT touch anything referencing `shastaRunnerAzureRepo`.

- [ ] **Step 3: Remove the `bin/platform.ts` wiring**

In `platform/bin/platform.ts`, delete the line in the `new ApiStack(...)` props that reads:

```typescript
  shastaRunnerAzure:  scanStack.shastaRunnerAzure,
```

- [ ] **Step 4: Remove the prop from `ApiStackProps` and its uses**

In `platform/lib/api-stack.ts`:
- Delete the `shastaRunnerAzure: lambda.IFunction;` line from the `ApiStackProps` interface.
- Delete the `props.shastaRunnerAzure.grantInvoke(connectionsListFn);` line.
- Delete the `props.shastaRunnerAzure.grantInvoke(onboardingAzureCompleteFn);` line.

`props.shastaRunnerEntra` and `props.shastaRunnerGcp` and their `grantInvoke` calls stay — those scanners are still Lambdas.

- [ ] **Step 5: Synth-check the whole app**

Run:
```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform && npx cdk synth >/dev/null && echo "synth OK"
```
Expected: `synth OK` — no TypeScript or synthesis errors across all stacks. A `ModuleNotFound` / `Property 'shastaRunnerAzure' does not exist` error means a reference was missed — fix it.

- [ ] **Step 6: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lib/scan-stack.ts platform/bin/platform.ts platform/lib/api-stack.ts
git commit -m "$(cat <<'EOF'
chore: retire the legacy single-pass Azure scanner Lambda

The v2 Fargate scanner fully replaces it — onboarding and rescan now
both ecs:RunTask. Removes the AzureRunner DockerImageFunction and its
cross-stack wiring. The shasta-runner-azure ECR repo stays (the Fargate
task def uses it).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Deploy

The legacy-Lambda removal drops a cross-stack export (`ScanStack` exported the Azure Lambda's ARN to `ApiStack`). CloudFormation refuses to delete an exported output while another stack still imports it — so `ApiStack` must be deployed **first** (it stops importing), then `ScanStack` (the export can be dropped). This is the documented two-phase `--exclusively` pattern.

**Files:** none (deploy + verification).

- [ ] **Step 1: Deploy the API stack first**

Run:
```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform && \
  npx cdk deploy CisoCopilotApi --exclusively --require-approval never
```
Expected: completes successfully. This ships the new `onboarding_azure_complete` + `connections_list` Lambda code and the new env vars / IAM, and removes `ApiStack`'s import of the legacy Azure Lambda.

- [ ] **Step 2: Deploy the scan stack second**

Run:
```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform && \
  npx cdk deploy CisoCopilotScan --exclusively --require-approval never
```
Expected: completes successfully — the legacy `AzureRunner` Lambda is deleted and its now-unused export is dropped. If this fails with an export-still-in-use error, Step 1 did not fully land — re-run Step 1, confirm it succeeded, then retry.

- [ ] **Step 3: Confirm the legacy Lambda is gone**

Run:
```bash
aws lambda get-function --function-name ciso-copilot-shasta-runner-azure 2>&1 | tail -2
```
Expected: a `ResourceNotFoundException` — the function no longer exists.

---

## Task 6: E2E verify — rescan through the real API path

Trigger an Azure rescan through the production `POST /connections/{id}/rescan` path (by direct-invoking the `connections_list` Lambda with a synthetic API-Gateway event — the same technique used to verify Slice 0) and confirm a v2 Fargate scan runs.

**Files:** none.

- [ ] **Step 1: Resolve the connections Lambda name and the Azure connection**

The Azure connection is `conn_id 79964b99-6501-413d-8f22-0431e870184d` (tenant `99d08352-53dd-4b59-beed-92cc755cb802`). Get the deployed `connections_list` function name:
```bash
aws lambda list-functions --query "Functions[?contains(FunctionName,'ConnectionsList')].FunctionName" --output text
```

Get a valid `sso_subject` for that tenant (the synthetic event needs it — `_resolve_tenant_id` looks the tenant up by the Cognito `sub`):
```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT sso_subject FROM users WHERE tenant_id = CAST('99d08352-53dd-4b59-beed-92cc755cb802' AS UUID) LIMIT 1"
```

- [ ] **Step 2: Invoke the rescan**

Write the synthetic event to `/tmp/azure_rescan_event.json` (substitute `<SSO_SUBJECT>` from Step 1):
```json
{
  "httpMethod": "POST",
  "path": "/connections/79964b99-6501-413d-8f22-0431e870184d/rescan",
  "pathParameters": {"id": "79964b99-6501-413d-8f22-0431e870184d"},
  "body": "{\"tier\": \"quick\"}",
  "requestContext": {"authorizer": {"claims": {"sub": "<SSO_SUBJECT>"}}}
}
```
Then invoke:
```bash
aws lambda invoke --function-name <CONNECTIONS_LIST_FN> \
  --payload fileb:///tmp/azure_rescan_event.json /tmp/azure_rescan_out.json \
  --cli-read-timeout 60 && cat /tmp/azure_rescan_out.json
```
Expected: a `200` response with `{"scan_id": "<uuid>", "status": "queued"}`.

- [ ] **Step 3: Watch the scan complete**

Poll the scan row (use the `scan_id` from Step 2):
```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT status, phase, tier, jsonb_typeof(scope) AS scope_type, (stats->>'findings') AS findings FROM scans WHERE scan_id = CAST('<SCAN_ID>' AS UUID)"
```
Expected: the scan reaches `status = completed` (or `partial`), `phase = done`, `tier = quick`, `scope_type = object`, `findings` > 0 — confirming the production rescan path now starts the v2 Fargate scanner. If the scan fails, read the Fargate task logs (the `azure-scan` log group).

- [ ] **Step 4: Update HANDOFF.md and commit**

Add an entry under the Azure Scanner Uplift section of `HANDOFF.md`: Slice 1b is complete — `onboarding_azure_complete` and the `_rescan_azure` path now start the v2 Azure Fargate scanner via `ecs:RunTask` (one task per connection, all subscriptions), the legacy Azure Lambda is retired, and the rescan path was live-verified. Note that the Azure scanner is now fully reachable from the product; the remaining Azure-uplift work is Slice 2 (the web subscription picker). Commit:

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add HANDOFF.md
git commit -m "$(cat <<'EOF'
docs: record Azure-uplift Slice 1b (production triggers on Fargate)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Done criteria

- [ ] `onboarding_azure_complete` and `connections_list._rescan_azure` start one `ciso-copilot-azure-scan` Fargate task per connection (all subscriptions, one `scans` row) — no `lambda.invoke` of an Azure runner remains.
- [ ] The rescan dispatcher passes the requested tier to `_rescan_azure`.
- [ ] Both Lambdas have `AZURE_SCAN_TASK_DEF` + the shared scan-infra env vars and the `ecs:RunTask` / `iam:PassRole` IAM for the Azure task def.
- [ ] The legacy `ciso-copilot-shasta-runner-azure` Lambda no longer exists; `cdk synth` is green across all stacks.
- [ ] A rescan triggered through the real `POST /connections/{id}/rescan` path completed as a v2 Fargate scan.
- [ ] No change to `connections_list`'s Entra/GCP rescan paths (still Lambda-based).
