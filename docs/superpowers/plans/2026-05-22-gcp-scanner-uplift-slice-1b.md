# GCP Scanner Uplift — Slice 1b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the v2 GCP Fargate scanner into the production trigger paths (onboarding + rescan) and retire the legacy GCP Lambda.

**Architecture:** `onboarding_gcp_complete` and `connections_list._rescan_gcp` start one `ciso-copilot-gcp-scan` Fargate task per connection via `ecs:RunTask`, replacing the legacy `lambda.invoke` of `ciso-copilot-shasta-runner-gcp`. The rescan path becomes tier-aware. The legacy `GcpRunner` Lambda is then removed from the CDK Scan stack via a clean two-phase deploy (Api stack first to drop the cross-stack imports, then Scan stack to drop the Lambda + its orphaned exports). This mirrors the Azure Slice 1b that already shipped.

**Tech Stack:** Python 3.12 (Lambda), AWS CDK (TypeScript), ECS Fargate, Aurora Data API.

**Spec:** `docs/superpowers/specs/2026-05-22-gcp-scanner-uplift-design.md` §7
**Predecessor:** Slice 1a (merged to `main`) — the v2 GCP scanner + `ciso-copilot-gcp-scan` Fargate task def already exist and are live-verified.

---

## Background an implementer needs

- **Slice 1a is done and merged.** The `ciso-copilot-gcp-scan` Fargate task def exists (CDK `lib/scan-stack.ts`, family `ciso-copilot-gcp-scan`), the v2 scanner image is in ECR, and a manual `ecs run-task` Quick scan was verified end-to-end. Slice 1b only changes *who triggers it* and removes the dead Lambda.
- **The Azure Slice 1b is the exact reference.** `onboarding_azure_complete/main.py` (`_run_initial_scan`) and `connections_list/main.py` (`_rescan_azure`) already do precisely this for Azure. Read them — the GCP versions should parallel them.
- **The GCP Fargate task's env-var contract** (from Slice 1a's `run.py`): `SCAN_ID`, `TENANT_ID`, `CONN_ID`, `PROJECT_IDS` (comma-separated), `WIF_PROJECT_NUMBER`, `SA_EMAIL`, `WIF_POOL`, `WIF_PROVIDER`, `SCAN_TIER`. The container name in the task def is `scanner`.
- **A GCP connection's `scope`** (single-project onboarding) is `{"project_id", "project_number", "sa_email", "wif_pool", "wif_provider"}`. Map: `PROJECT_IDS`←`project_id`, `WIF_PROJECT_NUMBER`←`project_number`, the rest 1:1.
- **The cross-stack deadlock to avoid** (paid in debugging time on Azure 1b): `ApiStack` currently imports `scanStack.shastaRunnerGcp` (the legacy Lambda) — that creates CloudFormation exports. You cannot delete an export from `ScanStack` while `ApiStack` still imports it. The fix is a two-phase deploy: deploy `CisoCopilotApi` first (Task 6) so it stops importing the Lambda, *then* deploy `CisoCopilotScan` to remove the Lambda and its now-orphaned exports. The new GCP wiring must create **zero** new cross-stack exports — use the literal task-def family string `'ciso-copilot-gcp-scan'` and IAM name-patterns, exactly as the Azure wiring does.
- **`connections_list` and the onboarding Lambdas have no unit tests** — they are verified structurally (`cdk synth`, AST parse) and by a live API call. This is the existing project pattern; do not add a test framework for them.
- CDK synth check: `cd platform && npx cdk synth <Stack> > /dev/null && echo OK`.

## File structure

```
platform/lambda/onboarding_gcp_complete/main.py   MODIFIED — Lambda invoke → ecs:RunTask
platform/lambda/connections_list/main.py          MODIFIED — _rescan_gcp → ecs:RunTask, tier-aware
platform/lib/api-stack.ts                         MODIFIED — GCP env/IAM rewired; drop shastaRunnerGcp import; add gcpScanTaskDefFamily prop
platform/bin/platform.ts                          MODIFIED — drop shastaRunnerGcp; add gcpScanTaskDefFamily
platform/lib/scan-stack.ts                        MODIFIED — retire the legacy GcpRunner Lambda
HANDOFF.md                                        MODIFIED — record Slice 1b
```

---

### Task 1: `onboarding_gcp_complete` — start the Fargate task

**File:** `platform/lambda/onboarding_gcp_complete/main.py`

- [ ] **Step 1: Replace the env-var constants and clients block**

Find this block (around lines 24-30):

```python
DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
GCP_RUNNER_FN  = os.environ.get("GCP_RUNNER_FN", "")

rds_data       = boto3.client("rds-data")
lambda_client  = boto3.client("lambda")
```

Replace it with:

```python
DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
GCP_SCAN_TASK_DEF      = os.environ.get("GCP_SCAN_TASK_DEF", "")
SCAN_CLUSTER_ARN       = os.environ.get("SCAN_CLUSTER_ARN", "")
SCAN_SUBNET_IDS        = os.environ.get("SCAN_SUBNET_IDS", "")
SCAN_SECURITY_GROUP_ID = os.environ.get("SCAN_SECURITY_GROUP_ID", "")

rds_data = boto3.client("rds-data")
ecs      = boto3.client("ecs")
```

- [ ] **Step 2: Replace `_enqueue_initial_scan` with a Fargate-launching `_run_initial_scan`**

Replace the entire `_enqueue_initial_scan` function (from `def _enqueue_initial_scan(` through its closing `return scan_id`) with:

```python
def _run_initial_scan(*, tenant_id: str, conn_id: str, scope: dict) -> str | None:
    """Insert one `scans` row and start one v2 GCP Fargate task that
    scans the connection's project. Mirrors onboarding_azure_complete."""
    if not (GCP_SCAN_TASK_DEF and SCAN_CLUSTER_ARN and SCAN_SUBNET_IDS):
        print("WARN: gcp scan task not configured; skipping initial scan")
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
                        {"name": "CONN_ID",            "value": conn_id},
                        {"name": "PROJECT_IDS",        "value": scope["project_id"]},
                        {"name": "WIF_PROJECT_NUMBER", "value": scope["project_number"]},
                        {"name": "SA_EMAIL",           "value": scope["sa_email"]},
                        {"name": "WIF_POOL",           "value": scope["wif_pool"]},
                        {"name": "WIF_PROVIDER",       "value": scope["wif_provider"]},
                        {"name": "SCAN_TIER",          "value": "quick"},
                    ],
                }],
            },
        )
        print(f"gcp onboarding scan {scan_id} started for {conn_id}")
    except Exception as e:
        print(f"WARN: gcp scan RunTask failed for {conn_id}: {e}")
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql="UPDATE scans SET status='failed' WHERE scan_id = CAST(:sid AS UUID)",
            parameters=[{"name": "sid", "value": {"stringValue": scan_id}}],
        )

    return scan_id
```

- [ ] **Step 3: Update the call site**

In `handler`, find the call (around line 86):

```python
    initial_scan_id = _enqueue_initial_scan(
        tenant_id      = conn["tenant_id"],
        conn_id        = conn["conn_id"],
        scope          = scope,
    )
```

Replace with:

```python
    initial_scan_id = _run_initial_scan(
        tenant_id = conn["tenant_id"],
        conn_id   = conn["conn_id"],
        scope     = scope,
    )
```

- [ ] **Step 4: Verify the module parses**

Run: `cd platform/lambda/onboarding_gcp_complete && python3 -c "import ast; ast.parse(open('main.py').read()); print('parses OK')"`
Expected: `parses OK`. Also confirm no remaining reference to `GCP_RUNNER_FN`, `lambda_client`, or `_enqueue_initial_scan`: `grep -n 'GCP_RUNNER_FN\|lambda_client\|_enqueue_initial_scan' main.py` should print nothing.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/onboarding_gcp_complete/main.py
git commit -m "feat: gcp onboarding starts the v2 Fargate scan task"
```

---

### Task 2: `connections_list._rescan_gcp` — Fargate + tier-aware

**File:** `platform/lambda/connections_list/main.py`

- [ ] **Step 1: Add the `GCP_SCAN_TASK_DEF` env constant**

Find the env-var block (around lines 36-42):

```python
ENTRA_RUNNER_FN  = os.environ.get("ENTRA_RUNNER_FN", "")
GCP_RUNNER_FN    = os.environ.get("GCP_RUNNER_FN", "")
SCAN_CLUSTER_ARN       = os.environ.get("SCAN_CLUSTER_ARN", "")
SCAN_TASK_DEF_ARN      = os.environ.get("SCAN_TASK_DEF_ARN", "")
AZURE_SCAN_TASK_DEF    = os.environ.get("AZURE_SCAN_TASK_DEF", "")
SCAN_SUBNET_IDS        = os.environ.get("SCAN_SUBNET_IDS", "")
SCAN_SECURITY_GROUP_ID = os.environ.get("SCAN_SECURITY_GROUP_ID", "")
```

Replace it with (drop `GCP_RUNNER_FN`, add `GCP_SCAN_TASK_DEF`):

```python
ENTRA_RUNNER_FN  = os.environ.get("ENTRA_RUNNER_FN", "")
SCAN_CLUSTER_ARN       = os.environ.get("SCAN_CLUSTER_ARN", "")
SCAN_TASK_DEF_ARN      = os.environ.get("SCAN_TASK_DEF_ARN", "")
AZURE_SCAN_TASK_DEF    = os.environ.get("AZURE_SCAN_TASK_DEF", "")
GCP_SCAN_TASK_DEF      = os.environ.get("GCP_SCAN_TASK_DEF", "")
SCAN_SUBNET_IDS        = os.environ.get("SCAN_SUBNET_IDS", "")
SCAN_SECURITY_GROUP_ID = os.environ.get("SCAN_SECURITY_GROUP_ID", "")
```

- [ ] **Step 2: Make the rescan dispatch pass `tier` to `_rescan_gcp`**

Find the dispatch line (around line 159):

```python
        elif cloud == "gcp":
            scan_id = _rescan_gcp(conn, tenant_id)
```

Replace with:

```python
        elif cloud == "gcp":
            scan_id = _rescan_gcp(conn, tenant_id, tier)
```

- [ ] **Step 3: Rewrite `_rescan_gcp`**

Replace the entire `_rescan_gcp` function (from `def _rescan_gcp(` through its closing `return scan_id`) with:

```python
def _rescan_gcp(conn: dict, tenant_id: str, tier: str) -> str:
    """Start one v2 GCP Fargate scan at `tier`. Mirrors _rescan_azure."""
    if not (GCP_SCAN_TASK_DEF and SCAN_CLUSTER_ARN and SCAN_SUBNET_IDS):
        raise _IncompleteConnection("gcp scan task not configured")

    scope = conn.get("scope") or {}
    required = ("project_id", "project_number", "sa_email", "wif_pool", "wif_provider")
    missing = [k for k in required if not scope.get(k)]
    if missing:
        raise _IncompleteConnection(f"missing scope fields: {','.join(missing)}")

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
                        {"name": "PROJECT_IDS",        "value": scope["project_id"]},
                        {"name": "WIF_PROJECT_NUMBER", "value": scope["project_number"]},
                        {"name": "SA_EMAIL",           "value": scope["sa_email"]},
                        {"name": "WIF_POOL",           "value": scope["wif_pool"]},
                        {"name": "WIF_PROVIDER",       "value": scope["wif_provider"]},
                        {"name": "SCAN_TIER",          "value": tier},
                    ],
                }],
            },
        )
        print(f"gcp rescan {scan_id} ({tier}) started for {conn['conn_id']}")
    except Exception as e:
        print(f"WARN: gcp rescan RunTask failed for {conn['conn_id']}: {e}")
        rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql="UPDATE scans SET status='failed' WHERE scan_id = CAST(:sid AS UUID)",
            parameters=[{"name": "sid", "value": {"stringValue": scan_id}}],
        )
    return scan_id
```

- [ ] **Step 4: Verify the module parses**

Run: `cd platform/lambda/connections_list && python3 -c "import ast; ast.parse(open('main.py').read()); print('parses OK')"`
Expected: `parses OK`. Confirm no remaining `GCP_RUNNER_FN` reference: `grep -n 'GCP_RUNNER_FN' main.py` prints nothing.

Note: `_invoke_async` (the legacy async-Lambda helper) is still used by `_rescan_entra` — leave it in place.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/connections_list/main.py
git commit -m "feat: gcp rescan starts the v2 Fargate scan task, tier-aware"
```

---

### Task 3: `api-stack.ts` — rewire GCP env/IAM, drop the Lambda import

**File:** `platform/lib/api-stack.ts`

- [ ] **Step 1: Add the `gcpScanTaskDefFamily` prop, remove `shastaRunnerGcp`**

In the `ApiStackProps` interface, find:

```typescript
  shastaRunnerGcp:    lambda.IFunction;
```

Replace it with:

```typescript
  gcpScanTaskDefFamily:             string;
```

(Place it next to `azureScanTaskDefFamily` if you prefer; either location compiles. Verify `shastaRunnerEntra` is still declared — only `shastaRunnerGcp` is being removed.)

- [ ] **Step 2: Rewire `connectionsListFn`**

Find the `connectionsListFn` environment block:

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

Replace the `GCP_RUNNER_FN` line with a `GCP_SCAN_TASK_DEF` line:

```typescript
      environment: {
        ...dbEnv,
        AZURE_SCAN_TASK_DEF:    props.azureScanTaskDefFamily,
        ENTRA_RUNNER_FN:        props.shastaRunnerEntra.functionName,
        GCP_SCAN_TASK_DEF:      props.gcpScanTaskDefFamily,
        SCAN_CLUSTER_ARN:       props.scanCluster.clusterArn,
        SCAN_TASK_DEF_ARN:      props.scanTaskDefFamily,
        SCAN_SUBNET_IDS:        props.vpc.privateSubnets.map(s => s.subnetId).join(','),
        SCAN_SECURITY_GROUP_ID: props.scanTaskSecurityGroupId,
      },
```

Then find and DELETE this line (the GCP Lambda grant):

```typescript
    props.shastaRunnerGcp.grantInvoke(connectionsListFn);
```

Update the comment just above it — find:

```typescript
    // Rescan dispatches into the Entra + GCP scanner Lambdas (AWS + Azure
    // rescans use ecs:RunTask) + reads/deletes the per-connection secret.
    props.shastaRunnerEntra.grantInvoke(connectionsListFn);
```

Replace that comment with:

```typescript
    // Rescan dispatches into the Entra scanner Lambda (AWS / Azure / GCP
    // rescans use ecs:RunTask) + reads/deletes the per-connection secret.
    props.shastaRunnerEntra.grantInvoke(connectionsListFn);
```

- [ ] **Step 3: Add the GCP `ecs:RunTask` + `iam:PassRole` for `connectionsListFn`**

Find the existing Azure RunTask/PassRole block for `connectionsListFn`:

```typescript
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ecs:RunTask'],
      resources: [`arn:aws:ecs:${this.region}:${this.account}:task-definition/${props.azureScanTaskDefFamily}:*`],
    }));
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['iam:PassRole'],
      resources: [
        props.scanTaskDefTaskRoleArn,
        props.scanTaskDefExecutionRoleArn,
      ],
    }));
    // Azure scan task roles — name-pattern scoped (the Azure task def lives
    // in the Scan stack; a name pattern avoids a cross-stack export).
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['iam:PassRole'],
      resources: [`arn:aws:iam::${this.account}:role/CisoCopilotScan-AzureScanTaskDef*`],
    }));
```

Immediately AFTER that block, insert:

```typescript
    // GCP scan task — RunTask on the gcp-scan family; PassRole for the
    // task role (the literal 'ciso-copilot-gcp-scanner' role) + the
    // CDK-named execution role (name-pattern scoped, no cross-stack export).
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ecs:RunTask'],
      resources: [`arn:aws:ecs:${this.region}:${this.account}:task-definition/${props.gcpScanTaskDefFamily}:*`],
    }));
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['iam:PassRole'],
      resources: [
        `arn:aws:iam::${this.account}:role/ciso-copilot-gcp-scanner`,
        `arn:aws:iam::${this.account}:role/CisoCopilotScan-GcpScanTaskDef*`,
      ],
    }));
```

- [ ] **Step 4: Rewire `onboardingGcpCompleteFn`**

Find the `onboardingGcpCompleteFn` definition:

```typescript
    const onboardingGcpCompleteFn = new lambda.Function(this, 'OnboardingGcpCompleteFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'onboarding_gcp_complete')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        ...dbEnv,
        GCP_RUNNER_FN: props.shastaRunnerGcp.functionName,
      },
    });
    props.dbCluster.grantDataApiAccess(onboardingGcpCompleteFn);
    props.shastaRunnerGcp.grantInvoke(onboardingGcpCompleteFn);
```

Replace it entirely with:

```typescript
    const onboardingGcpCompleteFn = new lambda.Function(this, 'OnboardingGcpCompleteFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'onboarding_gcp_complete')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        ...dbEnv,
        GCP_SCAN_TASK_DEF:      props.gcpScanTaskDefFamily,
        SCAN_CLUSTER_ARN:       props.scanCluster.clusterArn,
        SCAN_SUBNET_IDS:        props.vpc.privateSubnets.map(s => s.subnetId).join(','),
        SCAN_SECURITY_GROUP_ID: props.scanTaskSecurityGroupId,
      },
    });
    props.dbCluster.grantDataApiAccess(onboardingGcpCompleteFn);
    onboardingGcpCompleteFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ecs:RunTask'],
      resources: [`arn:aws:ecs:${this.region}:${this.account}:task-definition/${props.gcpScanTaskDefFamily}:*`],
    }));
    onboardingGcpCompleteFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['iam:PassRole'],
      resources: [
        `arn:aws:iam::${this.account}:role/ciso-copilot-gcp-scanner`,
        `arn:aws:iam::${this.account}:role/CisoCopilotScan-GcpScanTaskDef*`,
      ],
    }));
```

- [ ] **Step 5: Synth-check (still imports `shastaRunnerGcp` via `bin` — expected to fail until Task 4)**

Run: `cd platform && npx tsc --noEmit -p . 2>&1 | grep -i 'api-stack' || echo "api-stack.ts: no type errors"`
Expected: `api-stack.ts: no type errors` — the file itself is internally consistent. (A full `cdk synth` will still fail until `bin/platform.ts` is updated in Task 4; that is expected — do not try to synth yet.)

- [ ] **Step 6: Commit**

```bash
git add platform/lib/api-stack.ts
git commit -m "feat: api stack wires GCP rescan/onboarding to the Fargate task def"
```

---

### Task 4: `bin/platform.ts` — drop `shastaRunnerGcp`, add `gcpScanTaskDefFamily`

**File:** `platform/bin/platform.ts`

- [ ] **Step 1: Update the `ApiStack` props**

In the `new ApiStack(app, 'CisoCopilotApi', { ... })` call, find:

```typescript
  shastaRunnerGcp:    scanStack.shastaRunnerGcp,
```

Delete that line.

Then find:

```typescript
  azureScanTaskDefFamily:           'ciso-copilot-azure-scan',
```

Immediately after it, add:

```typescript
  gcpScanTaskDefFamily:             'ciso-copilot-gcp-scan',
```

- [ ] **Step 2: Synth-check `CisoCopilotApi`**

Run: `cd platform && npx cdk synth CisoCopilotApi > /dev/null && echo "cdk synth CisoCopilotApi OK"`
Expected: `cdk synth CisoCopilotApi OK` — the Api stack now compiles and synthesizes without importing the GCP Lambda.

- [ ] **Step 3: Commit**

```bash
git add platform/bin/platform.ts
git commit -m "feat: bin passes gcpScanTaskDefFamily, drops the GCP Lambda prop"
```

---

### Task 5: `scan-stack.ts` — retire the legacy GcpRunner Lambda

**File:** `platform/lib/scan-stack.ts`

The legacy `GcpRunner` `DockerImageFunction` is no longer invoked by anything (Tasks 1-4 removed every caller). Remove it. **Keep** `gcpScannerRole` (the Fargate task def uses it as `taskRole`) and the `shastaRunnerGcpRepo` ECR repo (the Fargate task def pulls its image).

- [ ] **Step 1: Remove the `shastaRunnerGcp` public field**

Find and DELETE this field declaration (around line 44):

```typescript
  public readonly shastaRunnerGcp:    lambda.DockerImageFunction;
```

- [ ] **Step 2: Remove the `GcpRunner` Lambda, keep the Data API grant on the role**

Find this block (around lines 235-244):

```typescript
    this.shastaRunnerGcp = new lambda.DockerImageFunction(this, 'GcpRunner', {
      functionName: 'ciso-copilot-shasta-runner-gcp',
      role:         gcpScannerRole,
      code: lambda.DockerImageCode.fromEcr(props.shastaRunnerGcpRepo, { tagOrDigest: 'latest' }),
      timeout:    cdk.Duration.minutes(15),
      memorySize: 2048,
      architecture: lambda.Architecture.X86_64,
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(this.shastaRunnerGcp);
```

Replace it with (the legacy Lambda is gone; the Data API grant moves directly onto `gcpScannerRole` so the Fargate task — which uses that role — keeps Aurora access):

```typescript
    // The v2 GCP scanner runs as the Fargate task defined below; the
    // legacy GcpRunner Lambda has been retired (Slice 1b). gcpScannerRole
    // is still the GCP scan identity (the Fargate task's taskRole), so
    // grant Aurora Data API access directly on the role.
    props.dbCluster.grantDataApiAccess(gcpScannerRole);
```

- [ ] **Step 3: Fix the now-stale comment in the GcpScanTaskDef block**

Find this comment block (it referenced the Lambda's grant, which no longer exists):

```typescript
    // gcpScannerRole already has Data API access granted via the
    // GcpRunner Lambda's grantDataApiAccess call above — the Fargate
    // task shares the same role, so no extra grant is needed. The WIF
    // GetCallerIdentity call requires no IAM policy (a principal may
    // always describe itself).
```

Replace it with:

```typescript
    // gcpScannerRole has Aurora Data API access granted directly above.
    // The WIF GetCallerIdentity call requires no IAM policy (a principal
    // may always describe itself).
```

- [ ] **Step 4: Remove the legacy Lambda's CfnOutputs**

Find and DELETE these two output lines (around lines 339-340):

```typescript
    new cdk.CfnOutput(this, 'ShastaRunnerGcpArn',      { value: this.shastaRunnerGcp.functionArn });
    new cdk.CfnOutput(this, 'ShastaRunnerGcpFnName',   { value: this.shastaRunnerGcp.functionName });
```

KEEP the `ShastaRunnerGcpRoleArn` output (the role still exists).

- [ ] **Step 5: Confirm no remaining references**

Run: `cd platform && grep -n 'shastaRunnerGcp\b\|GcpRunner\|ShastaRunnerGcpArn\|ShastaRunnerGcpFnName' lib/scan-stack.ts`
Expected: only `shastaRunnerGcpRepo` (the ECR repo prop — still used by the Fargate task def) may appear. No `GcpRunner`, no `this.shastaRunnerGcp`, no removed outputs.

- [ ] **Step 6: Synth-check `CisoCopilotScan`**

Run: `cd platform && npx cdk synth CisoCopilotScan > /dev/null && echo "cdk synth CisoCopilotScan OK"`
Expected: `cdk synth CisoCopilotScan OK`.

- [ ] **Step 7: Commit**

```bash
git add platform/lib/scan-stack.ts
git commit -m "chore: retire the legacy GCP scanner Lambda"
```

---

### Task 6: Two-phase deploy + live-verify a GCP rescan

This is the verification gate. The deploy order is load-bearing — `CisoCopilotApi` MUST deploy before `CisoCopilotScan`.

- [ ] **Step 1: Deploy `CisoCopilotApi` first**

Run: `cd platform && npx cdk deploy CisoCopilotApi --require-approval never`
Expected: deploys successfully. After this, `ApiStack` no longer imports the GCP Lambda — the cross-stack exports on it are now orphaned but harmless.

- [ ] **Step 2: Deploy `CisoCopilotScan` second**

Run: `cd platform && npx cdk deploy CisoCopilotScan --require-approval never`
Expected: deploys successfully; the `GcpRunner` Lambda (`ciso-copilot-shasta-runner-gcp`) and its two outputs are removed. If CloudFormation reports an export still in use, STOP — it means Step 1 did not fully drop the import; do not force it.

- [ ] **Step 3: Confirm the legacy Lambda is gone**

Run: `aws lambda get-function --function-name ciso-copilot-shasta-runner-gcp 2>&1 | grep -q 'ResourceNotFoundException' && echo "legacy GCP Lambda retired" || echo "STILL EXISTS"`
Expected: `legacy GCP Lambda retired`.

- [ ] **Step 4: Live-verify a rescan through the real trigger code**

The GCP connection: `conn_id` `219f41eb-311c-42a8-8e76-c473a7dbf3b4`, `tenant_id` `99d08352-53dd-4b59-beed-92cc755cb802` (confirm it is still the most-recent active GCP connection with: `aws rds-data execute-statement --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp --database ciso_copilot --sql "SELECT conn_id, tenant_id FROM cloud_connections WHERE cloud_type='gcp' AND status='active' ORDER BY created_at DESC LIMIT 1" --output json`).

Invoke `ConnectionsListFn` directly with a synthetic API Gateway proxy event for `POST /connections/{id}/rescan` — this exercises the real `_rescan` → `_rescan_gcp` → `ecs:RunTask` path (`_resolve_tenant_id` reads the Cognito claims from `requestContext.authorizer.claims`):

```bash
cat > /tmp/gcp-rescan-event.json <<'EOF'
{
  "httpMethod": "POST",
  "path": "/connections/219f41eb-311c-42a8-8e76-c473a7dbf3b4/rescan",
  "pathParameters": {"id": "219f41eb-311c-42a8-8e76-c473a7dbf3b4"},
  "body": "{\"tier\":\"quick\"}",
  "requestContext": {
    "authorizer": {
      "claims": {
        "custom:tenant_id": "99d08352-53dd-4b59-beed-92cc755cb802",
        "email": "kkmookhey@gmail.com"
      }
    }
  }
}
EOF
aws lambda invoke --function-name "$(aws cloudformation describe-stacks --stack-name CisoCopilotApi --query "Stacks[0].Outputs[?contains(OutputKey,'ConnectionsList')].OutputValue" --output text 2>/dev/null || echo ConnectionsListFn)" --payload fileb:///tmp/gcp-rescan-event.json /tmp/gcp-rescan-resp.json --cli-binary-format raw-in-base64-out >/dev/null && cat /tmp/gcp-rescan-resp.json
```

Expected: the response body is `{"scan_id": "<uuid>", "status": "queued"}` with `statusCode` 200.

IMPORTANT: `_resolve_tenant_id` may read the tenant claim under a different key than `custom:tenant_id`. Before running, open `platform/lambda/connections_list/main.py`, read `_resolve_tenant_id`, and adjust the `claims` keys in the event to match exactly what it reads. If the function name lookup fails, find it with `aws lambda list-functions --query "Functions[?contains(FunctionName,'ConnectionsList')].FunctionName" --output text`.

- [ ] **Step 5: Poll the scan to completion**

Take the `scan_id` from Step 4. Poll:

```bash
SCAN_ID=<scan_id from step 4>
until aws rds-data execute-statement --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp --database ciso_copilot --sql "SELECT status, phase FROM scans WHERE scan_id=CAST('$SCAN_ID' AS UUID)" --output json | python3 -c "import sys,json; r=json.load(sys.stdin)['records'][0]; s=r[0]['stringValue']; print('status=',s,'phase=',r[1].get('stringValue')); sys.exit(0 if s in ('completed','failed','partial') else 1)"; do sleep 20; done
```

Expected: terminates at `status=completed` (or `partial`), `phase=done`. Then confirm findings landed:

```bash
aws rds-data execute-statement --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp --database ciso_copilot --sql "SELECT count(*) FROM findings WHERE scan_id=CAST('$SCAN_ID' AS UUID)" --output json
```

Expected: a non-zero finding count — confirming the rescan trigger started the Fargate task and it ran end-to-end.

- [ ] **Step 6: Update HANDOFF.md**

In the "GCP Scanner Uplift" section of `HANDOFF.md`, record Slice 1b as shipped: onboarding + rescan now start the `ciso-copilot-gcp-scan` Fargate task via `ecs:RunTask`; rescan is tier-aware; the legacy `ciso-copilot-shasta-runner-gcp` Lambda is retired; the two-phase deploy (Api then Scan) is done; cite the live-verified rescan `scan_id` and its finding count. Note Slice 2a (org onboarding) and 2b (the Scan-screen brainstorm, then the picker) remain.

- [ ] **Step 7: Commit**

```bash
git add HANDOFF.md
git commit -m "docs: record GCP scanner uplift Slice 1b shipped"
```

---

## Self-review

**Spec coverage** (`2026-05-22-gcp-scanner-uplift-design.md` §7):
- "onboarding_gcp_complete and _rescan_gcp start one ciso-copilot-gcp-scan Fargate task via ecs:RunTask" — Tasks 1, 2.
- "_rescan_gcp becomes tier-aware" — Task 2 (signature `(conn, tenant_id, tier)`, dispatch passes `tier`).
- "legacy GcpRunner DockerImageFunction retired via the two-phase deploy (Api first, then Scan)" — Tasks 3-5 drop the imports, Task 6 deploys in order.
- "shasta-runner-gcp ECR repo stays" — Task 5 keeps `shastaRunnerGcpRepo`.
- "zero new cross-stack export churn — literal task-def family, iam:PassRole by role-name pattern" — Task 3 uses the literal `'ciso-copilot-gcp-scan'` string and the `ciso-copilot-gcp-scanner` literal + `CisoCopilotScan-GcpScanTaskDef*` pattern; no new `CfnOutput`/`Fn::ImportValue` is introduced.

**Placeholder scan:** the `<scan_id from step 4>` token in Task 6 is a runtime value; all else is concrete. The Task 6 Step 4 note to verify `_resolve_tenant_id`'s claim key is a deliberate runtime check, not an unresolved placeholder.

**Consistency check:** the GCP Fargate env-var names (`PROJECT_IDS`, `WIF_PROJECT_NUMBER`, `SA_EMAIL`, `WIF_POOL`, `WIF_PROVIDER`, `SCAN_TIER`, `SCAN_ID`, `TENANT_ID`, `CONN_ID`) in Tasks 1 and 2 exactly match Slice 1a's `run.py` `_REQUIRED` set and `build_event`. The `GCP_SCAN_TASK_DEF` env var is set by api-stack (Task 3) and read by both Lambdas (Tasks 1, 2). The `gcpScanTaskDefFamily` prop is declared in `ApiStackProps` (Task 3), passed in `bin` (Task 4), and the value `'ciso-copilot-gcp-scan'` matches the task-def `family` set in `scan-stack.ts` (Slice 1a). The Data API grant on `gcpScannerRole` (Task 5) replaces the grant lost when the Lambda is removed — without it the Fargate task would lose Aurora access.

No issues found.
