# AI-Powered SOC — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship AWS Config infra drift end-to-end. Demo: open a security group to `0.0.0.0/0:22` on a connected AWS account → within 60s the iPhone vibrates with a templated push, tapping through to a new `/soc` web page that shows the drift event with an AI-written narrative + anomaly classification + suggested next steps.

**Architecture:** Extends the existing `event_router` Lambda (already deployed, already normalizes Config + CloudTrail events into Aurora `events`+`drift_events`). Adds: idempotency via `source_event_id`, Config-specific severity rule table, before/after state extraction, push rule evaluation with rate limit, SQS enqueue to a new `soc-enrichment-queue`. A new `soc_enrichment` Lambda consumes the queue, computes statistical features, calls LiteLLM (default `claude-sonnet-4-6`), and writes `ai_*` fields back to the events row. A new `/soc` web page renders the timeline + detail pane via the extended `events_list` API.

**Tech Stack:** Python 3.12 + boto3 (Lambdas), LiteLLM (LLM client), AWS Lambda + SQS + DynamoDB + EventBridge + Aurora Serverless v2 PG, AWS CDK (TypeScript), React + TypeScript + Vitest (web). No new infra primitives — all on the existing pattern.

**Spec:** `docs/superpowers/specs/2026-05-25-ai-powered-soc-design.md` — sections §3 (slice plan), §4 (architecture + latency budget), §5 (components), §6 (data model), §7 (data flow), §8 (error handling), §9 (testing), §10 (customer onboarding).

**Predecessor:** Current `main`. Existing scaffolding already in place — confirm before starting:
- `platform/lib/events-stack.ts` deploys central EventBridge bus `ciso-copilot-events` + raw events S3 + event router Lambda
- `platform/lambda/event_router/main.py` runs the pipeline shell (tenant resolve → S3 archive → normalize → events INSERT → drift_events INSERT) — Config + CloudTrail normalization is present; push + dedupe + AI are NOT
- `platform/cfn/aws-onboard.yaml` already ships AWS Config recorder + EventBridge forwarding rule for security events; this slice refines the Config recording profile but doesn't add new resource types
- `platform/lambda/events_list/main.py` already serves `GET /events`; this slice extends the response shape and adds detail + feedback endpoints

---

## File Structure

**Creates:**
- `platform/sql/005_phase_soc.sql` — migration for AI fields + `source_event_id` + indices on `events`
- `platform/lambda/event_router/tests/__init__.py` — new test package
- `platform/lambda/event_router/tests/test_dedupe.py` — `source_event_id` extraction + ON CONFLICT
- `platform/lambda/event_router/tests/test_severity_rules.py` — Config drift severity rule table
- `platform/lambda/event_router/tests/test_push.py` — push threshold + rate-limit + SNS call
- `platform/lambda/event_router/severity_rules.py` — Config + CloudTrail-IAM severity rule tables
- `platform/lambda/event_router/push.py` — push evaluation + SNS Mobile Push call
- `platform/lambda/event_router/spend_cap.py` — shared DynamoDB rate-limit helper (used by push + enrichment)
- `platform/lambda/soc_enrichment/__init__.py`
- `platform/lambda/soc_enrichment/main.py` — handler
- `platform/lambda/soc_enrichment/features.py` — statistical features
- `platform/lambda/soc_enrichment/llm.py` — LiteLLM wrapper + prompt template
- `platform/lambda/soc_enrichment/parser.py` — response parser + Aurora UPDATE
- `platform/lambda/soc_enrichment/requirements.txt` — declares `litellm`
- `platform/lambda/soc_enrichment/build.sh` — packages with litellm into Lambda zip
- `platform/lambda/soc_enrichment/tests/__init__.py`
- `platform/lambda/soc_enrichment/tests/test_features.py`
- `platform/lambda/soc_enrichment/tests/test_llm.py`
- `platform/lambda/soc_enrichment/tests/test_parser.py`
- `platform/lambda/events_list/tests/__init__.py`
- `platform/lambda/events_list/tests/test_list.py` — list endpoint + AI field projection
- `platform/lambda/events_list/tests/test_detail.py` — detail endpoint
- `platform/lambda/events_list/tests/test_feedback.py` — feedback endpoint
- `web/src/routes/Soc.tsx` — main page
- `web/src/routes/Soc.test.tsx` — Vitest
- `web/src/components/soc/Timeline.tsx`
- `web/src/components/soc/FilterChips.tsx`
- `web/src/components/soc/DetailPane.tsx`
- `web/src/components/soc/FeedbackButtons.tsx`
- `docs/customer/drift-detection-aws.md`

**Modifies:**
- `platform/lib/events-stack.ts` — add SQS `soc-enrichment-queue` + DLQ, DynamoDB `soc_llm_spend_daily` table, soc_enrichment Lambda + SQS event source, grant queue write to router
- `platform/lib/api-stack.ts` — wire `GET /events/{event_id}` + `POST /events/{event_id}/feedback`; extend `events_list` env to surface AI fields (no change actually needed in api-stack — env wiring is in the Lambda; just add the two new methods + resources)
- `platform/lambda/event_router/main.py` — add `source_event_id` extraction, ON CONFLICT, before/after state, push call, SQS enqueue
- `platform/lambda/events_list/main.py` — project AI fields in list response; add detail + feedback handlers (multi-method routing inside the same Lambda OR via separate handlers — see Task 11)
- `platform/cfn/aws-onboard.yaml` — add `RecordingMode` parameter (default `essentials`); when essentials, RecordingGroup uses explicit `resourceTypes` list of ~25 security-critical types instead of `AllSupported: true`
- `web/src/App.tsx` or router file — add `/soc` route + nav link
- `TEST_PLAN.md` — append Slice 1 manual gate

**Total surface:** ~32 files created, ~6 files modified.

---

## Task 1: Schema migration for AI + dedupe fields

**Files:**
- Create: `platform/sql/005_phase_soc.sql`

**Context:** Spec §6 defines the schema deltas. Existing `events`/`drift_events` tables in `002_phase_a.sql:96-127`. Self-review during spec writing caught two issues — `source_event_id` column is absent and one index already exists; this migration handles both.

- [ ] **Step 1: Write the migration SQL**

Create `platform/sql/005_phase_soc.sql`:

```sql
-- 005_phase_soc.sql — AI-powered SOC sub-project Slice 1 schema migration
-- Refs: docs/superpowers/specs/2026-05-25-ai-powered-soc-design.md §6

-- AI enrichment fields on events (populated async by soc_enrichment Lambda)
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_narrative      TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_anomaly_class  TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_anomaly_score  INTEGER;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_next_steps     JSONB;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_features       JSONB;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_model_version  TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_enriched_at    TIMESTAMPTZ;

-- Kill-chain pre-commitments (nullable; future correlator populates)
ALTER TABLE events ADD COLUMN IF NOT EXISTS mitre_technique   TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS incident_id       UUID;

-- Idempotency: provider-native event ID + unique constraint
ALTER TABLE events ADD COLUMN IF NOT EXISTS source_event_id   TEXT;
-- Unique only when source_event_id is present (legacy rows have NULL)
CREATE UNIQUE INDEX IF NOT EXISTS uq_events_tenant_source_sei
  ON events (tenant_id, source, source_event_id)
  WHERE source_event_id IS NOT NULL;

-- Drift graph-shape pre-commitment (redundant with events.resource_arn — explicit for future entity graph)
ALTER TABLE drift_events ADD COLUMN IF NOT EXISTS target_resource_arn TEXT;

-- Query indices for /soc (idx_events_tenant_kind_fired already exists in 002_phase_a.sql:114)
CREATE INDEX IF NOT EXISTS idx_events_tenant_anomaly
  ON events (tenant_id, ai_anomaly_class, fired_at DESC)
  WHERE ai_anomaly_class IN ('unusual','suspicious');

CREATE INDEX IF NOT EXISTS idx_events_incident
  ON events (incident_id) WHERE incident_id IS NOT NULL;
```

- [ ] **Step 2: Execute the migration against Aurora**

Run:
```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "$(cat platform/sql/005_phase_soc.sql)"
```

Expected: empty `records` array, no error. (`IF NOT EXISTS` makes this idempotent.)

- [ ] **Step 3: Verify columns + indices present**

Run:
```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT column_name FROM information_schema.columns WHERE table_name='events' AND column_name IN ('ai_narrative','source_event_id','mitre_technique','incident_id') ORDER BY column_name"
```

Expected: 4 rows — `ai_narrative`, `incident_id`, `mitre_technique`, `source_event_id`.

- [ ] **Step 4: Commit**

```bash
git add platform/sql/005_phase_soc.sql
git commit -m "feat(soc-s1): schema migration for AI fields + source_event_id + indices"
```

---

## Task 2: CDK — SQS enrichment queue, DLQ, DynamoDB spend-cap table

**Files:**
- Modify: `platform/lib/events-stack.ts`

**Context:** The router enqueues one SQS message per drift event for the new `soc_enrichment` Lambda. The DynamoDB spend-cap table tracks per-tenant daily LLM spend in cents. Both belong in `EventsStack` since they're part of the event pipeline.

- [ ] **Step 1: Add SQS + DynamoDB imports + constructs**

Edit `platform/lib/events-stack.ts`. Add to imports at top:

```typescript
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as sqsEventSource from 'aws-cdk-lib/aws-lambda-event-sources';
```

Add new public readonly fields to the `EventsStack` class:

```typescript
  public readonly enrichmentQueue: sqs.Queue;
  public readonly enrichmentDlq:   sqs.Queue;
  public readonly spendCapTable:   dynamodb.Table;
```

Add after `this.routerFn = ...` block (around line 75, after the router env block) and BEFORE the `FanToRouter` rule (around line 88):

```typescript
    // ============================================================
    // SOC enrichment queue (DLQ + main) — router enqueues, soc_enrichment consumes
    // ============================================================
    this.enrichmentDlq = new sqs.Queue(this, 'SocEnrichmentDlq', {
      queueName:       'soc-enrichment-dlq',
      retentionPeriod: cdk.Duration.days(14),
    });
    this.enrichmentQueue = new sqs.Queue(this, 'SocEnrichmentQueue', {
      queueName:         'soc-enrichment-queue',
      visibilityTimeout: cdk.Duration.seconds(120),  // > Lambda timeout (90s)
      deadLetterQueue:   { queue: this.enrichmentDlq, maxReceiveCount: 3 },
    });

    // ============================================================
    // Per-tenant daily LLM spend counter (cents)
    // ============================================================
    this.spendCapTable = new dynamodb.Table(this, 'SocLlmSpendDaily', {
      tableName: 'soc_llm_spend_daily',
      partitionKey: { name: 'tenant_id', type: dynamodb.AttributeType.STRING },
      sortKey:      { name: 'day',       type: dynamodb.AttributeType.STRING },  // 'YYYY-MM-DD'
      billingMode:  dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expires_at',  // numeric unix-ts; we set this to day+90d
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Grant router permission to enqueue
    this.enrichmentQueue.grantSendMessages(this.routerFn);
    this.routerFn.addEnvironment('ENRICHMENT_QUEUE_URL', this.enrichmentQueue.queueUrl);
    this.routerFn.addEnvironment('SPEND_CAP_TABLE_NAME', this.spendCapTable.tableName);
    this.spendCapTable.grantReadWriteData(this.routerFn);  // router writes push-rate-limit counters; same table
```

Add outputs at the bottom:

```typescript
    new cdk.CfnOutput(this, 'EnrichmentQueueUrl',  { value: this.enrichmentQueue.queueUrl });
    new cdk.CfnOutput(this, 'EnrichmentDlqUrl',    { value: this.enrichmentDlq.queueUrl });
    new cdk.CfnOutput(this, 'SpendCapTableName',   { value: this.spendCapTable.tableName });
```

- [ ] **Step 2: Synth + deploy**

Run from `platform/`:
```bash
npx cdk synth CisoCopilotEvents > /dev/null && npx cdk deploy CisoCopilotEvents --require-approval never
```

Expected: stack updates with 3 new resources (`SocEnrichmentDlq`, `SocEnrichmentQueue`, `SocLlmSpendDaily`) + IAM policy attachment + router env update. No drift on `CentralEventBus`, `RawEventsBucket`, `EventRouter`.

- [ ] **Step 3: Verify deployment**

Run:
```bash
aws sqs get-queue-url --queue-name soc-enrichment-queue
aws dynamodb describe-table --table-name soc_llm_spend_daily --query 'Table.TableStatus'
```

Expected: queue URL printed; table status `ACTIVE`.

- [ ] **Step 4: Commit**

```bash
git add platform/lib/events-stack.ts
git commit -m "feat(soc-s1): SQS enrichment queue + DLQ + DynamoDB spend-cap table"
```

---

## Task 3: Failing tests — `source_event_id` extraction + ON CONFLICT dedupe

**Files:**
- Create: `platform/lambda/event_router/tests/__init__.py` (empty)
- Create: `platform/lambda/event_router/tests/conftest.py`
- Create: `platform/lambda/event_router/tests/test_dedupe.py`

**Context:** Spec §4.3 operational guarantee #4: idempotent ingestion. Same provider-native event arriving twice = exactly one events row. CloudTrail emits a stable `eventID` in the detail; AWS Config events use `configurationItemCaptureTime + resourceId` as a composite. Router will compute the `source_event_id` per source, INSERT with ON CONFLICT DO NOTHING, return early if conflict.

- [ ] **Step 1: Create conftest with sample event fixtures**

Create `platform/lambda/event_router/tests/conftest.py`:

```python
"""Sample EventBridge events for tests."""
from __future__ import annotations
import pytest


@pytest.fixture
def cloudtrail_sg_open_event() -> dict:
    """A real-shape CloudTrail event for AuthorizeSecurityGroupIngress :22 to 0.0.0.0/0."""
    return {
        "version":     "0",
        "id":          "ebr-event-abc123",
        "detail-type": "AWS API Call via CloudTrail",
        "source":      "aws.cloudtrail",
        "account":     "$AWS_ACCOUNT_ID",
        "time":        "2026-05-25T18:42:10Z",
        "region":      "us-east-1",
        "detail": {
            "eventID":          "ct-eventid-7f3a9c",   # the stable provider ID
            "eventName":        "AuthorizeSecurityGroupIngress",
            "eventSource":      "ec2.amazonaws.com",
            "userIdentity":     {"arn": "arn:aws:iam::$AWS_ACCOUNT_ID:user/test-user"},
            "requestParameters": {
                "groupId": "sg-0abc123def",
                "ipPermissions": {"items": [{"ipProtocol": "tcp", "fromPort": 22, "toPort": 22,
                                             "ipRanges": {"items": [{"cidrIp": "0.0.0.0/0"}]}}]},
            },
            "resources":       [{"ARN": "arn:aws:ec2:us-east-1:$AWS_ACCOUNT_ID:security-group/sg-0abc123def"}],
        },
    }


@pytest.fixture
def config_item_change_event() -> dict:
    """A real-shape AWS Config item change event."""
    return {
        "version":     "0",
        "id":          "ebr-event-xyz789",
        "detail-type": "Configuration Item Change Notification",
        "source":      "aws.config",
        "account":     "$AWS_ACCOUNT_ID",
        "time":        "2026-05-25T18:43:15Z",
        "region":      "us-east-1",
        "detail": {
            "configurationItem": {
                "configurationItemCaptureTime": "2026-05-25T18:43:14.123Z",
                "configurationItemStatus":     "OK",
                "configurationStateId":        "1716658994123",
                "resourceType":                "AWS::EC2::SecurityGroup",
                "resourceId":                  "sg-0abc123def",
                "ARN":                         "arn:aws:ec2:us-east-1:$AWS_ACCOUNT_ID:security-group/sg-0abc123def",
                "configuration":               {"ipPermissions": [{"fromPort": 22, "toPort": 22,
                                                                   "ipRanges": [{"cidrIp": "0.0.0.0/0"}]}]},
            },
            "configurationItemDiff": {
                "changeType": "UPDATE",
                "changedProperties": {
                    "Configuration.IpPermissions.0": {
                        "previousValue": [],
                        "updatedValue":  [{"fromPort": 22, "toPort": 22,
                                          "ipRanges": [{"cidrIp": "0.0.0.0/0"}]}],
                        "changeType":    "UPDATE",
                    },
                },
            },
        },
    }
```

- [ ] **Step 2: Write failing test for source_event_id extraction**

Create `platform/lambda/event_router/tests/test_dedupe.py`:

```python
"""Test that source_event_id is extracted per source and that dedupe SQL is emitted."""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


def test_source_event_id_cloudtrail(cloudtrail_sg_open_event):
    """CloudTrail events get source_event_id = detail.eventID."""
    sei = main._source_event_id(cloudtrail_sg_open_event)
    assert sei == "ct-eventid-7f3a9c"


def test_source_event_id_config(config_item_change_event):
    """Config events get source_event_id = configurationItemCaptureTime + resourceId."""
    sei = main._source_event_id(config_item_change_event)
    assert sei == "2026-05-25T18:43:14.123Z:sg-0abc123def"


def test_source_event_id_unknown_returns_none():
    """Unknown source falls back to None (legacy path; INSERT without dedupe)."""
    evt = {"source": "unknown", "detail": {}}
    assert main._source_event_id(evt) is None


def test_insert_event_emits_on_conflict_do_nothing(monkeypatch):
    """_insert_event uses ON CONFLICT (tenant_id, source, source_event_id) DO NOTHING."""
    captured = {}
    class FakeRdsData:
        def execute_statement(self, **kwargs):
            captured["sql"]    = kwargs["sql"]
            captured["params"] = kwargs["parameters"]
            return {"records": []}
    monkeypatch.setattr(main, "rds_data", FakeRdsData())

    main._insert_event(
        event_id="e1", tenant_id="t1", conn_id="c1",
        kind="drift", source="aws.cloudtrail", severity="high",
        title="SG opened", description=None, resource_arn="sg-1", actor="user/x",
        raw_s3_key="raw/2026/05/25/t1/aws.cloudtrail/e1.json",
        normalized={"x": 1}, fired_at="2026-05-25T18:42:10Z",
        source_event_id="ct-eventid-7f3a9c",
    )

    assert "ON CONFLICT" in captured["sql"]
    assert "DO NOTHING" in captured["sql"]
    # source_event_id must be a parameter
    names = {p["name"] for p in captured["params"]}
    assert "sei" in names
```

- [ ] **Step 3: Run tests — expect FAIL**

Run:
```bash
cd platform/lambda/event_router && python -m pytest tests/ -v
```

Expected: `AttributeError: module 'main' has no attribute '_source_event_id'` or similar. All four tests fail.

- [ ] **Step 4: Commit failing tests**

```bash
git add platform/lambda/event_router/tests/
git commit -m "test(soc-s1): failing tests for source_event_id + ON CONFLICT dedupe"
```

---

## Task 4: Implement `source_event_id` extraction + ON CONFLICT INSERT

**Files:**
- Modify: `platform/lambda/event_router/main.py`

**Context:** Tests from Task 3 drive this. Add a `_source_event_id` function with per-source branches, and modify `_insert_event` to accept `source_event_id` and include `ON CONFLICT ... DO NOTHING` in the SQL. Make `handler` pass the extracted value through.

- [ ] **Step 1: Add `_source_event_id` function**

In `platform/lambda/event_router/main.py`, add after `_classify_kind` (around line 219):

```python
def _source_event_id(event: dict) -> str | None:
    """Return a stable per-source idempotency key, or None for unknown sources."""
    source = event.get("source", "")
    detail = event.get("detail", {}) or {}

    if source == "aws.cloudtrail":
        return detail.get("eventID")
    if source == "aws.config":
        ci = detail.get("configurationItem", {}) or {}
        capture = ci.get("configurationItemCaptureTime")
        rid     = ci.get("resourceId")
        return f"{capture}:{rid}" if capture and rid else None
    if source == "aws.guardduty":
        return detail.get("id")
    if source == "aws.inspector2":
        return (detail.get("findingArn") or "").split("/")[-1] or None
    if source == "aws.securityhub":
        first = (detail.get("findings") or [{}])[0]
        return first.get("Id")
    return None
```

- [ ] **Step 2: Modify `_insert_event` signature + SQL**

Replace the existing `_insert_event` function (lines ~249-293) with:

```python
def _insert_event(
    *,
    event_id: str,
    tenant_id: str,
    conn_id: str,
    kind: str,
    source: str,
    severity: str,
    title: str,
    description: str | None,
    resource_arn: str | None,
    actor: str | None,
    raw_s3_key: str,
    normalized: dict,
    fired_at: str,
    source_event_id: str | None,
) -> bool:
    """INSERT into events with ON CONFLICT DO NOTHING. Returns True if inserted, False if dup."""
    result = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "INSERT INTO events (event_id, tenant_id, conn_id, kind, source, severity, "
            "                    title, description, resource_arn, actor, raw_s3_key, "
            "                    normalized, fired_at, source_event_id) "
            "VALUES (CAST(:eid AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), "
            "        :kind, :source, :severity, :title, :description, :resource_arn, "
            "        :actor, :raw_s3_key, CAST(:normalized AS JSONB), "
            "        CAST(:fired_at AS TIMESTAMPTZ), :sei) "
            "ON CONFLICT (tenant_id, source, source_event_id) DO NOTHING "
            "RETURNING event_id"
        ),
        parameters=[
            {"name": "eid",          "value": {"stringValue": event_id}},
            {"name": "tid",          "value": {"stringValue": tenant_id}},
            {"name": "cid",          "value": {"stringValue": conn_id}},
            {"name": "kind",         "value": {"stringValue": kind}},
            {"name": "source",       "value": {"stringValue": source}},
            {"name": "severity",     "value": {"stringValue": severity}},
            {"name": "title",        "value": {"stringValue": title}},
            {"name": "description",  "value": ({"stringValue": description} if description else {"isNull": True})},
            {"name": "resource_arn", "value": ({"stringValue": resource_arn} if resource_arn else {"isNull": True})},
            {"name": "actor",        "value": ({"stringValue": actor} if actor else {"isNull": True})},
            {"name": "raw_s3_key",   "value": {"stringValue": raw_s3_key}},
            {"name": "normalized",   "value": {"stringValue": json.dumps(normalized)}},
            {"name": "fired_at",     "value": {"stringValue": fired_at}},
            {"name": "sei",          "value": ({"stringValue": source_event_id} if source_event_id else {"isNull": True})},
        ],
    )
    return len(result.get("records", [])) > 0
```

- [ ] **Step 3: Update `handler` to pass `source_event_id` and short-circuit on dup**

In `handler`, replace the existing `_insert_event(...)` call (around line 63-77) with:

```python
        source_event_id = _source_event_id(event)

        inserted = _insert_event(
            event_id        = event_id,
            tenant_id       = conn["tenant_id"],
            conn_id         = conn["conn_id"],
            kind            = kind,
            source          = source,
            severity        = severity,
            title           = normalized.get("title", detail_type or source),
            description     = normalized.get("description"),
            resource_arn    = normalized.get("resource_arn"),
            actor           = normalized.get("actor"),
            raw_s3_key      = raw_s3_key,
            normalized      = normalized,
            fired_at        = fired_at,
            source_event_id = source_event_id,
        )

        if not inserted:
            print(f"DROP: duplicate (tenant={conn['tenant_id']}, source={source}, sei={source_event_id})")
            return {"ok": True, "deduped": True}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd platform/lambda/event_router && python -m pytest tests/ -v
```

Expected: 4 passed.

- [ ] **Step 5: Hotswap deploy + smoke**

```bash
cd platform && npx cdk deploy CisoCopilotEvents --require-approval never --hotswap
```

Verify the router still runs by checking CloudWatch logs over the last 10 minutes for `/aws/lambda/CisoCopilotEvents-EventRouter*`:
```bash
aws logs tail "$(aws lambda list-functions --query 'Functions[?starts_with(FunctionName, `CisoCopilotEvents-EventRouter`)].FunctionName' --output text)" --since 10m | head -20
```

Expected: no new ERROR entries; `{"router": "received", ...}` lines present.

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/event_router/main.py
git commit -m "feat(soc-s1): source_event_id extraction + ON CONFLICT DO NOTHING dedupe"
```

---

## Task 5: Config severity rule table + before/after state extraction

**Files:**
- Create: `platform/lambda/event_router/severity_rules.py`
- Create: `platform/lambda/event_router/tests/test_severity_rules.py`
- Modify: `platform/lambda/event_router/main.py` — replace inline `_severity` for cloudtrail/config + extend `_insert_drift`

**Context:** Spec §5 says router emits deterministic severity from a rule table. Spec §6 commits `drift_events.before_state`/`after_state` to graph-shape pre-commitment. Current code (`main.py:240-242`) defaults all CloudTrail+Config drift to `medium` with a stale comment about a "push-rule layer" promoting; we replace that with an explicit rule table and extract before/after from `configurationItemDiff` for Config events.

- [ ] **Step 1: Failing tests for severity rule table**

Create `platform/lambda/event_router/tests/test_severity_rules.py`:

```python
"""Severity rule table: action → severity floor for drift events."""
import severity_rules


def test_sg_open_to_world_is_high():
    assert severity_rules.drift_severity(
        action="AuthorizeSecurityGroupIngress",
        after={"ipPermissions": [{"fromPort": 22, "toPort": 22,
                                  "ipRanges": [{"cidrIp": "0.0.0.0/0"}]}]},
    ) == "high"


def test_sg_open_db_port_to_world_is_critical():
    assert severity_rules.drift_severity(
        action="AuthorizeSecurityGroupIngress",
        after={"ipPermissions": [{"fromPort": 3306, "toPort": 3306,
                                  "ipRanges": [{"cidrIp": "0.0.0.0/0"}]}]},
    ) == "critical"


def test_mfa_deactivate_is_critical():
    assert severity_rules.drift_severity(action="DeactivateMFADevice", after={}) == "critical"


def test_root_console_login_is_critical():
    assert severity_rules.drift_severity(
        action="ConsoleLogin",
        after={"userIdentity": {"type": "Root"}},
    ) == "critical"


def test_iam_attach_admin_policy_is_high():
    assert severity_rules.drift_severity(
        action="AttachUserPolicy",
        after={"policyArn": "arn:aws:iam::aws:policy/AdministratorAccess"},
    ) == "high"


def test_bucket_public_acl_is_high():
    assert severity_rules.drift_severity(
        action="PutBucketAcl",
        after={"accessControlPolicy": {"grants": [{"grantee": {"uri": "http://acs.amazonaws.com/groups/global/AllUsers"}}]}},
    ) == "high"


def test_unknown_action_defaults_to_low():
    assert severity_rules.drift_severity(action="SomeBoringAction", after={}) == "low"


def test_action_in_rule_but_after_doesnt_match_pattern_defaults_to_medium():
    # SG ingress but bound to a private range: rule fires medium not high/critical
    assert severity_rules.drift_severity(
        action="AuthorizeSecurityGroupIngress",
        after={"ipPermissions": [{"fromPort": 22, "toPort": 22,
                                  "ipRanges": [{"cidrIp": "10.0.0.0/8"}]}]},
    ) == "medium"
```

Run: `cd platform/lambda/event_router && python -m pytest tests/test_severity_rules.py -v`
Expected: `ModuleNotFoundError: No module named 'severity_rules'`.

- [ ] **Step 2: Implement severity_rules.py**

Create `platform/lambda/event_router/severity_rules.py`:

```python
"""Deterministic severity rule table for drift events.

Each rule = (action, predicate(after) → severity). First match wins.
Actions not in the table default to 'low' (drift on uninteresting resources).
Actions in the table whose predicates don't match default to 'medium' (the
action is interesting but the specific change isn't load-bearing).
"""
from __future__ import annotations
from typing import Any, Callable


# === Predicates over the `after_state` JSON ===

def _ipranges_include_world(after: dict) -> bool:
    for perm in after.get("ipPermissions", []) or []:
        for r in perm.get("ipRanges", []) or []:
            if r.get("cidrIp") in ("0.0.0.0/0", "::/0"):
                return True
    return False


def _has_db_port(after: dict) -> bool:
    DB_PORTS = {1433, 1521, 3306, 5432, 5984, 6379, 9200, 27017}
    for perm in after.get("ipPermissions", []) or []:
        for p in range(perm.get("fromPort", 0), perm.get("toPort", -1) + 1):
            if p in DB_PORTS:
                return True
    return False


def _is_root_login(after: dict) -> bool:
    return ((after.get("userIdentity") or {}).get("type") == "Root")


def _attaches_admin_policy(after: dict) -> bool:
    arn = after.get("policyArn", "")
    return arn.endswith("/AdministratorAccess") or arn.endswith("/PowerUserAccess")


def _bucket_public_grant(after: dict) -> bool:
    grants = ((after.get("accessControlPolicy") or {}).get("grants")) or []
    for g in grants:
        uri = (g.get("grantee") or {}).get("uri", "")
        if "AllUsers" in uri or "AuthenticatedUsers" in uri:
            return True
    return False


# === Rule table — order matters (first match wins within an action) ===

_RULES: dict[str, list[tuple[Callable[[dict], bool], str]]] = {
    "AuthorizeSecurityGroupIngress": [
        (lambda a: _ipranges_include_world(a) and _has_db_port(a), "critical"),
        (_ipranges_include_world,                                  "high"),
    ],
    "AuthorizeSecurityGroupEgress": [
        (_ipranges_include_world, "high"),
    ],
    "DeactivateMFADevice":    [(lambda a: True, "critical")],
    "DeleteVirtualMFADevice": [(lambda a: True, "critical")],
    "ConsoleLogin":           [(_is_root_login, "critical")],
    "CreateLoginProfile":     [(lambda a: True, "high")],
    "UpdateLoginProfile":     [(lambda a: True, "high")],
    "CreateAccessKey":        [(lambda a: True, "high")],
    "AttachUserPolicy":       [(_attaches_admin_policy, "high")],
    "AttachRolePolicy":       [(_attaches_admin_policy, "high")],
    "PutUserPolicy":          [(lambda a: True, "medium")],
    "PutRolePolicy":          [(lambda a: True, "medium")],
    "PutBucketAcl":           [(_bucket_public_grant, "high")],
    "PutBucketPolicy":        [(lambda a: True, "medium")],
    "DeletePublicAccessBlock":[(lambda a: True, "high")],
    "DisableKey":             [(lambda a: True, "high")],
    "ScheduleKeyDeletion":    [(lambda a: True, "high")],
}


def drift_severity(*, action: str, after: dict) -> str:
    """Look up severity for a drift action. See module docstring for fallback semantics."""
    rules = _RULES.get(action)
    if rules is None:
        return "low"
    for predicate, sev in rules:
        if predicate(after):
            return sev
    return "medium"
```

Run: `cd platform/lambda/event_router && python -m pytest tests/test_severity_rules.py -v`
Expected: 8 passed.

- [ ] **Step 3: Wire `_severity` to call the rule table for drift events**

In `platform/lambda/event_router/main.py`, replace the `_severity` function (around line 221-242) with:

```python
from severity_rules import drift_severity


def _severity(source: str, detail: dict, kind: str, after_state: dict | None) -> str:
    """Normalize source-specific severities to {critical, high, medium, low, info}."""
    if source == "aws.guardduty":
        sev = detail.get("severity", 0)
        if sev >= 8: return "critical"
        if sev >= 7: return "high"
        if sev >= 4: return "medium"
        if sev >= 1: return "low"
        return "info"
    if source == "aws.inspector2":
        label = (detail.get("severity") or "").upper()
        return {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium",
                "LOW": "low", "INFORMATIONAL": "info"}.get(label, "info")
    if source == "aws.securityhub":
        finding = (detail.get("findings") or [{}])[0]
        label = ((finding.get("Severity") or {}).get("Label") or "").upper()
        return {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium",
                "LOW": "low", "INFORMATIONAL": "info"}.get(label, "info")

    # CloudTrail + Config drift use the rule table over after_state
    if kind == "drift":
        action = detail.get("eventName") or (detail.get("configurationItem") or {}).get("resourceType", "")
        return drift_severity(action=action, after=(after_state or {}))

    return "medium"
```

And in `handler`, replace `severity = _severity(source, event.get("detail", {}))` with:

```python
        before_state, after_state = _extract_states(source, event.get("detail", {}))
        severity = _severity(source, event.get("detail", {}), kind, after_state)
```

Add `_extract_states` after the `_classify_kind` function:

```python
def _extract_states(source: str, detail: dict) -> tuple[dict | None, dict | None]:
    """Return (before_state, after_state) JSON-like dicts for the drift extension. NULL/NULL for non-drift sources."""
    if source == "aws.config":
        ci   = detail.get("configurationItem", {}) or {}
        diff = detail.get("configurationItemDiff", {}) or {}
        after  = ci.get("configuration") or {}
        # Reconstruct before by applying inverse-diff to after — Config gives us the changed properties only.
        before: dict[str, Any] = {}
        for path, change in (diff.get("changedProperties") or {}).items():
            if "previousValue" in change:
                before[path] = change["previousValue"]
        return (before or None), (after or None)

    if source == "aws.cloudtrail":
        # CloudTrail doesn't give before; after = requestParameters (the desired post-state of the API call).
        return None, (detail.get("requestParameters") or None)

    return None, None
```

And update `_insert_drift` signature + SQL to persist the states:

```python
def _insert_drift(event_id: str, action: str, before_state: dict | None, after_state: dict | None,
                  target_resource_arn: str | None) -> None:
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql=(
            "INSERT INTO drift_events (event_id, action, before_state, after_state, target_resource_arn) "
            "VALUES (CAST(:eid AS UUID), :action, "
            "        CAST(:before AS JSONB), CAST(:after AS JSONB), :tgt) "
            "ON CONFLICT (event_id) DO NOTHING"
        ),
        parameters=[
            {"name": "eid",    "value": {"stringValue": event_id}},
            {"name": "action", "value": {"stringValue": action}},
            {"name": "before", "value": ({"stringValue": json.dumps(before_state)} if before_state else {"isNull": True})},
            {"name": "after",  "value": ({"stringValue": json.dumps(after_state)}  if after_state  else {"isNull": True})},
            {"name": "tgt",    "value": ({"stringValue": target_resource_arn}      if target_resource_arn else {"isNull": True})},
        ],
    )
```

In `handler`, replace `_insert_drift(event_id, normalized)` with:

```python
        if kind == "drift":
            action = event.get("detail", {}).get("eventName") or \
                     (event.get("detail", {}).get("configurationItem") or {}).get("resourceType", "drift")
            _insert_drift(event_id, action, before_state, after_state, normalized.get("resource_arn"))
```

- [ ] **Step 4: Run all router tests**

```bash
cd platform/lambda/event_router && python -m pytest tests/ -v
```

Expected: all green (4 dedupe tests + 8 severity tests = 12 passed).

- [ ] **Step 5: Hotswap deploy + smoke**

```bash
cd platform && npx cdk deploy CisoCopilotEvents --require-approval never --hotswap
```

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/event_router/severity_rules.py platform/lambda/event_router/tests/test_severity_rules.py platform/lambda/event_router/main.py
git commit -m "feat(soc-s1): Config drift severity rule table + before/after state extraction"
```

---

## Task 6: Push rule evaluation + per-tenant rate limit + SNS Mobile Push

**Files:**
- Create: `platform/lambda/event_router/spend_cap.py` — shared DynamoDB counter helper (reused by enrichment Lambda)
- Create: `platform/lambda/event_router/push.py` — push rule + SNS call
- Create: `platform/lambda/event_router/tests/test_push.py`
- Modify: `platform/lambda/event_router/main.py` — call push from handler after INSERT
- Modify: `platform/lib/events-stack.ts` — add `APNS_PLATFORM_APPLICATION_ARN` env to router; grant SNS publish

**Context:** Spec §4.3 push fires in <60s on the deterministic path; §8 "Event flood" — per-tenant rate limit default 10/hr. The `soc_llm_spend_daily` DynamoDB table from Task 2 doubles as a push-rate-limit counter using a different sort key (`push_count:YYYY-MM-DDTHH`). SNS Mobile Push uses an Apple Platform Application configured once (the .p8 key path) — assume the ARN is already in Secrets Manager from existing v1 work; if not, create one and add to env.

- [ ] **Step 1: Failing tests for push**

Create `platform/lambda/event_router/tests/test_push.py`:

```python
"""Push rule evaluation + rate limit + SNS Mobile Push call."""
import push


def test_should_push_critical_always_true():
    assert push.should_push("critical", current_hour_count=0) is True


def test_should_push_high_when_under_threshold():
    assert push.should_push("high", current_hour_count=0) is True


def test_should_push_medium_skipped_by_default():
    assert push.should_push("medium", current_hour_count=0) is False


def test_should_push_high_skipped_when_over_rate_limit():
    # default cap = 10/hr; this hour we've already pushed 10 → cap reached
    assert push.should_push("high", current_hour_count=10) is False


def test_should_push_critical_skipped_when_over_rate_limit():
    # criticals bypass the cap (operational safety — never silently drop a critical)
    assert push.should_push("critical", current_hour_count=999) is True


def test_format_push_body_drift():
    body = push.format_push_body(
        kind="drift", severity="high", title="AuthorizeSecurityGroupIngress",
        resource_arn="arn:aws:ec2:us-east-1:123:security-group/sg-abc",
        actor="arn:aws:iam::123:user/x",
    )
    assert "drift" in body.lower()
    assert "sg-abc" in body
    assert "user/x" in body


def test_send_push_calls_sns(monkeypatch):
    calls = []
    class FakeSns:
        def publish(self, **kw):
            calls.append(kw)
            return {"MessageId": "m-1"}
    monkeypatch.setattr(push, "sns", FakeSns())
    push.send_push(
        device_tokens=["device-token-aaa"],
        platform_app_arn="arn:aws:sns:us-east-1:123:app/APNS/test",
        body="hi",
    )
    assert len(calls) == 1
    assert calls[0]["TargetArn"].startswith("arn:aws:sns:")
```

Run: `cd platform/lambda/event_router && python -m pytest tests/test_push.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 2: Implement spend_cap.py (rate-limit + LLM-spend counter, shared)**

Create `platform/lambda/event_router/spend_cap.py`:

```python
"""DynamoDB-backed daily/hourly counters. Used by router (push rate limit)
and soc_enrichment (LLM spend cap). One table, two key shapes — keep the
helpers small."""
from __future__ import annotations
import os
import time
import boto3
from datetime import datetime, timezone

dynamodb  = boto3.client("dynamodb")
TABLE_NAME = os.environ.get("SPEND_CAP_TABLE_NAME", "soc_llm_spend_daily")


def _expires_at(days: int = 90) -> int:
    return int(time.time()) + days * 86400


def push_count_increment(tenant_id: str) -> int:
    """Increment this hour's push counter for the tenant. Returns the NEW count."""
    hour_key = datetime.now(timezone.utc).strftime("push_count:%Y-%m-%dT%H")
    rs = dynamodb.update_item(
        TableName=TABLE_NAME,
        Key={"tenant_id": {"S": tenant_id}, "day": {"S": hour_key}},
        UpdateExpression="ADD #c :one SET #exp = :exp",
        ExpressionAttributeNames={"#c": "count", "#exp": "expires_at"},
        ExpressionAttributeValues={
            ":one": {"N": "1"},
            ":exp": {"N": str(_expires_at())},
        },
        ReturnValues="UPDATED_NEW",
    )
    return int(rs["Attributes"]["count"]["N"])


def push_count_current(tenant_id: str) -> int:
    """Read this hour's push count without incrementing."""
    hour_key = datetime.now(timezone.utc).strftime("push_count:%Y-%m-%dT%H")
    rs = dynamodb.get_item(
        TableName=TABLE_NAME,
        Key={"tenant_id": {"S": tenant_id}, "day": {"S": hour_key}},
    )
    item = rs.get("Item")
    return int(item["count"]["N"]) if item and "count" in item else 0


def llm_spend_today_cents(tenant_id: str) -> int:
    day_key = datetime.now(timezone.utc).strftime("llm_spend:%Y-%m-%d")
    rs = dynamodb.get_item(
        TableName=TABLE_NAME,
        Key={"tenant_id": {"S": tenant_id}, "day": {"S": day_key}},
    )
    item = rs.get("Item")
    return int(item["cents"]["N"]) if item and "cents" in item else 0


def llm_spend_add(tenant_id: str, cents: int) -> int:
    day_key = datetime.now(timezone.utc).strftime("llm_spend:%Y-%m-%d")
    rs = dynamodb.update_item(
        TableName=TABLE_NAME,
        Key={"tenant_id": {"S": tenant_id}, "day": {"S": day_key}},
        UpdateExpression="ADD #c :n SET #exp = :exp",
        ExpressionAttributeNames={"#c": "cents", "#exp": "expires_at"},
        ExpressionAttributeValues={
            ":n":   {"N": str(int(cents))},
            ":exp": {"N": str(_expires_at())},
        },
        ReturnValues="UPDATED_NEW",
    )
    return int(rs["Attributes"]["cents"]["N"])
```

- [ ] **Step 3: Implement push.py**

Create `platform/lambda/event_router/push.py`:

```python
"""Push rule evaluation + SNS Mobile Push call."""
from __future__ import annotations
import json
import os
import boto3

sns = boto3.client("sns")

# Defaults — overridable per-tenant in a future slice; v1 is hardcoded.
PUSH_THRESHOLD       = "high"     # severity floor for non-critical pushes
PUSH_RATE_LIMIT_HOUR = 10         # criticals always bypass

_SEV_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def should_push(severity: str, current_hour_count: int) -> bool:
    """True if push should fire. Criticals always push; non-critical respects threshold + rate limit."""
    if severity == "critical":
        return True
    if _SEV_ORDER.get(severity, 0) < _SEV_ORDER[PUSH_THRESHOLD]:
        return False
    return current_hour_count < PUSH_RATE_LIMIT_HOUR


def format_push_body(*, kind: str, severity: str, title: str,
                     resource_arn: str | None, actor: str | None) -> str:
    """Templated one-liner. The AI narrative arrives at the GET; push is deterministic."""
    bits = [kind, severity]
    rid  = (resource_arn or "").split("/")[-1] or (resource_arn or "")
    if rid:
        bits.append(rid)
    bits.append(title)
    if actor:
        bits.append(f"by {actor.split('/')[-1]}")
    return " · ".join(bits)


def send_push(*, device_tokens: list[str], platform_app_arn: str, body: str) -> None:
    """Create per-device endpoints lazily and publish via SNS Mobile Push."""
    payload = {"aps": {"alert": body, "sound": "default"}}
    for token in device_tokens:
        # CreatePlatformEndpoint is idempotent for the same (platform-app, token).
        ep = sns.create_platform_endpoint(
            PlatformApplicationArn=platform_app_arn,
            Token=token,
        )
        sns.publish(
            TargetArn=ep["EndpointArn"],
            Message=json.dumps({"APNS": json.dumps(payload)}),
            MessageStructure="json",
        )
```

Run tests: `cd platform/lambda/event_router && python -m pytest tests/test_push.py -v`
Expected: 7 passed.

- [ ] **Step 4: Wire push into the router handler**

In `platform/lambda/event_router/main.py`, add imports at top:

```python
import push
import spend_cap
```

Add an env var read:

```python
APNS_PLATFORM_APP_ARN = os.environ.get("APNS_PLATFORM_APPLICATION_ARN", "")
```

Add a helper to fetch device tokens for the tenant:

```python
def _device_tokens_for_tenant(tenant_id: str) -> list[str]:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql="SELECT device_token FROM users WHERE tenant_id = CAST(:t AS UUID) AND device_token IS NOT NULL",
        parameters=[{"name": "t", "value": {"stringValue": tenant_id}}],
    )
    return [r[0].get("stringValue", "") for r in rs.get("records", []) if r[0].get("stringValue")]
```

In `handler`, after the successful `_insert_event` and `_insert_drift` block, add (before the `return`):

```python
        # 5. Push-rule evaluation
        try:
            current = spend_cap.push_count_current(conn["tenant_id"])
            if push.should_push(severity, current) and APNS_PLATFORM_APP_ARN:
                tokens = _device_tokens_for_tenant(conn["tenant_id"])
                if tokens:
                    body = push.format_push_body(
                        kind=kind, severity=severity,
                        title=normalized.get("title", ""),
                        resource_arn=normalized.get("resource_arn"),
                        actor=normalized.get("actor"),
                    )
                    push.send_push(device_tokens=tokens,
                                   platform_app_arn=APNS_PLATFORM_APP_ARN,
                                   body=body)
                    spend_cap.push_count_increment(conn["tenant_id"])
                    # Mark in events table so it's queryable
                    rds_data.execute_statement(
                        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
                        sql="UPDATE events SET push_sent = true WHERE event_id = CAST(:e AS UUID)",
                        parameters=[{"name": "e", "value": {"stringValue": event_id}}],
                    )
        except Exception as e:
            print(f"WARN: push failed (non-fatal): {e}")
```

- [ ] **Step 5: Grant SNS + add env in CDK**

In `platform/lib/events-stack.ts`, add after the existing IAM policy block:

```typescript
    this.routerFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['sns:CreatePlatformEndpoint', 'sns:Publish'],
      resources: ['*'],
    }));
    this.routerFn.addEnvironment('APNS_PLATFORM_APPLICATION_ARN',
      cdk.Fn.importValue('CisoCopilotAuth-ApnsPlatformApplicationArn'));
```

(Adjust the import name if the v1 auth/notifications stack exports it differently — grep for `ApnsPlatformApplicationArn` in `platform/lib/`.)

- [ ] **Step 6: Run tests + deploy**

```bash
cd platform/lambda/event_router && python -m pytest tests/ -v
cd ../../.. && npx cdk deploy CisoCopilotEvents --require-approval never
```

Expected: all router tests pass; CDK deploy adds IAM perms + env var only (no resource churn).

- [ ] **Step 7: Commit**

```bash
git add platform/lambda/event_router/push.py platform/lambda/event_router/spend_cap.py platform/lambda/event_router/tests/test_push.py platform/lambda/event_router/main.py platform/lib/events-stack.ts
git commit -m "feat(soc-s1): push-rule + per-tenant rate limit + SNS Mobile Push call"
```

---

## Task 7: SQS enqueue from router to enrichment queue

**Files:**
- Modify: `platform/lambda/event_router/main.py` — enqueue after successful INSERT for drift events

**Context:** Spec §4.1 — after `_insert_event` commits and push fires, router enqueues an SQS message that the enrichment Lambda will pick up. Only drift events are enriched in Slice 1 (alerts come in Slice 2+).

- [ ] **Step 1: Add SQS client + env var read at module top**

In `platform/lambda/event_router/main.py`, add imports/env at top:

```python
ENRICHMENT_QUEUE_URL = os.environ.get("ENRICHMENT_QUEUE_URL", "")
sqs = boto3.client("sqs")
```

- [ ] **Step 2: Enqueue after successful drift insert**

In `handler`, after the push block, add:

```python
        # 6. Enqueue for async AI enrichment (Slice 1 = drift only)
        if kind == "drift" and ENRICHMENT_QUEUE_URL:
            try:
                sqs.send_message(
                    QueueUrl=ENRICHMENT_QUEUE_URL,
                    MessageBody=json.dumps({"event_id": event_id, "tenant_id": conn["tenant_id"]}),
                )
            except Exception as e:
                print(f"WARN: enrichment enqueue failed (non-fatal, will rely on backfill): {e}")
```

- [ ] **Step 3: Deploy hotswap + smoke**

```bash
cd platform && npx cdk deploy CisoCopilotEvents --require-approval never --hotswap
```

Verify SQS is receiving by sending a synthetic event to the central bus:
```bash
aws events put-events --entries '[{"Source":"aws.config","DetailType":"Configuration Item Change Notification","Detail":"{\"configurationItem\":{\"configurationItemCaptureTime\":\"2026-05-25T19:00:00Z\",\"resourceId\":\"sg-test-soc-s1\",\"resourceType\":\"AWS::EC2::SecurityGroup\",\"ARN\":\"arn:aws:ec2:us-east-1:$AWS_ACCOUNT_ID:security-group/sg-test-soc-s1\",\"configuration\":{\"ipPermissions\":[{\"fromPort\":22,\"toPort\":22,\"ipRanges\":[{\"cidrIp\":\"0.0.0.0/0\"}]}]}},\"configurationItemDiff\":{\"changeType\":\"UPDATE\"}}","EventBusName":"ciso-copilot-events"}]'
sleep 5
aws sqs receive-message --queue-url $(aws sqs get-queue-url --queue-name soc-enrichment-queue --query QueueUrl --output text) --max-number-of-messages 1
```

Expected: a single message with `event_id` + `tenant_id` JSON body. (Note: tenant resolution may fail if the synthetic account isn't in `cloud_connections` — for the smoke, accept either a successful message OR a "no_connection" log entry.)

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/event_router/main.py
git commit -m "feat(soc-s1): router enqueues drift events to soc-enrichment-queue"
```

---

## Task 8: soc_enrichment Lambda scaffold + SQS trigger + happy-path UPDATE test

**Files:**
- Create: `platform/lambda/soc_enrichment/__init__.py` (empty)
- Create: `platform/lambda/soc_enrichment/main.py`
- Create: `platform/lambda/soc_enrichment/requirements.txt`
- Create: `platform/lambda/soc_enrichment/build.sh`
- Create: `platform/lambda/soc_enrichment/tests/__init__.py`
- Create: `platform/lambda/soc_enrichment/tests/conftest.py`
- Create: `platform/lambda/soc_enrichment/tests/test_main.py`
- Modify: `platform/lib/events-stack.ts` — add the Lambda + SQS event source + IAM

**Context:** The handler is invoked once per SQS message (or per batch of up to 10). For each message: fetch the events row, compute features (Task 9), call LiteLLM (Task 10), parse + UPDATE (Task 11). This task establishes the scaffold + the UPDATE plumbing so Tasks 9-11 can fill in the middle.

- [ ] **Step 1: requirements.txt + build.sh**

Create `platform/lambda/soc_enrichment/requirements.txt`:

```
litellm==1.51.0
boto3==1.35.0
```

Create `platform/lambda/soc_enrichment/build.sh`:

```bash
#!/usr/bin/env bash
# Build the soc_enrichment Lambda zip with litellm + boto3 bundled.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf build dist && mkdir -p build dist
pip install --target build -r requirements.txt --quiet
cp -r main.py features.py llm.py parser.py build/
cd build && zip -qr ../dist/soc_enrichment.zip . && cd ..
echo "Built $(pwd)/dist/soc_enrichment.zip"
```

Run: `chmod +x platform/lambda/soc_enrichment/build.sh`

- [ ] **Step 2: Failing test for handler skeleton + UPDATE plumbing**

Create `platform/lambda/soc_enrichment/tests/__init__.py` (empty).

Create `platform/lambda/soc_enrichment/tests/conftest.py`:

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest


@pytest.fixture
def sample_sqs_event() -> dict:
    return {
        "Records": [{
            "messageId": "msg-1",
            "body": '{"event_id": "11111111-1111-1111-1111-111111111111", "tenant_id": "22222222-2222-2222-2222-222222222222"}',
        }]
    }


@pytest.fixture
def sample_event_row() -> dict:
    return {
        "event_id":        "11111111-1111-1111-1111-111111111111",
        "tenant_id":       "22222222-2222-2222-2222-222222222222",
        "source":          "aws.config",
        "kind":            "drift",
        "severity":        "high",
        "title":           "AuthorizeSecurityGroupIngress",
        "actor":           "arn:aws:iam::$AWS_ACCOUNT_ID:user/test-user",
        "resource_arn":    "arn:aws:ec2:us-east-1:$AWS_ACCOUNT_ID:security-group/sg-abc",
        "fired_at":        "2026-05-25T18:42:10Z",
        "before_state":    {},
        "after_state":     {"ipPermissions": [{"fromPort": 22, "toPort": 22,
                                                "ipRanges": [{"cidrIp": "0.0.0.0/0"}]}]},
    }
```

Create `platform/lambda/soc_enrichment/tests/test_main.py`:

```python
import json
import main


def test_handler_processes_each_record(sample_sqs_event, sample_event_row, monkeypatch):
    """Handler loads the events row, runs the pipeline, and UPDATEs ai_* fields."""
    loads, updates = [], []

    monkeypatch.setattr(main, "_load_event_row",
                        lambda event_id, tenant_id: (loads.append((event_id, tenant_id)) or sample_event_row))
    monkeypatch.setattr(main, "compute_features",  lambda row: {"first_time_actor": True})
    monkeypatch.setattr(main, "call_llm",          lambda row, features: {
        "narrative": "Suspicious change to public SG.",
        "anomaly_class": "suspicious", "anomaly_score": 88,
        "next_steps": [{"step": "Revoke ingress", "command": "aws ec2 revoke-security-group-ingress ..."}],
        "mitre_technique": "T1098",
    })
    monkeypatch.setattr(main, "_update_event_ai", lambda **kw: updates.append(kw))

    main.handler(sample_sqs_event, None)

    assert loads == [("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222")]
    assert len(updates) == 1
    u = updates[0]
    assert u["narrative"] == "Suspicious change to public SG."
    assert u["anomaly_class"] == "suspicious"
    assert u["features"] == {"first_time_actor": True}


def test_handler_skips_missing_row(sample_sqs_event, monkeypatch):
    """If the events row vanished (TTL, race), log + return without UPDATE."""
    monkeypatch.setattr(main, "_load_event_row", lambda *_: None)
    updates = []
    monkeypatch.setattr(main, "_update_event_ai", lambda **kw: updates.append(kw))
    main.handler(sample_sqs_event, None)
    assert updates == []
```

Run: `cd platform/lambda/soc_enrichment && python -m pytest tests/ -v`
Expected: `ModuleNotFoundError: No module named 'main'`.

- [ ] **Step 3: Implement main.py scaffold**

Create `platform/lambda/soc_enrichment/main.py`:

```python
"""SOC enrichment Lambda — SQS consumer.

For each drift event:
  1. Load the events row from Aurora
  2. Compute statistical features (features.py)
  3. Call LiteLLM with prompt template (llm.py)
  4. Parse response + UPDATE events row with ai_* fields (parser.py)

Per spec §4.3: p95 enrichment <30s, hard timeout 90s.
"""
from __future__ import annotations

import json
import os
from typing import Any

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")


# Filled in by Tasks 9-10 — stubs so imports work and tests can monkeypatch.
def compute_features(row: dict) -> dict:  # noqa: D401
    return {}


def call_llm(row: dict, features: dict) -> dict:  # noqa: D401
    return {"narrative": None, "anomaly_class": None, "anomaly_score": None,
            "next_steps": None, "mitre_technique": None, "model_version": "stub"}


def handler(event: dict, context: Any) -> dict:
    for rec in event.get("Records", []):
        body = json.loads(rec["body"])
        event_id  = body["event_id"]
        tenant_id = body["tenant_id"]

        row = _load_event_row(event_id, tenant_id)
        if row is None:
            print(f"SKIP: event {event_id} not found (vanished)")
            continue

        try:
            features = compute_features(row)
            ai       = call_llm(row, features)
        except Exception as e:
            print(f"WARN: enrichment failed for {event_id}: {e}")
            _update_event_ai(event_id=event_id,
                             narrative=None, anomaly_class=None, anomaly_score=None,
                             next_steps=None, features=features if 'features' in dir() else {},
                             model_version="unavailable", mitre_technique=None)
            continue

        _update_event_ai(
            event_id        = event_id,
            narrative       = ai.get("narrative"),
            anomaly_class   = ai.get("anomaly_class"),
            anomaly_score   = ai.get("anomaly_score"),
            next_steps      = ai.get("next_steps"),
            features        = features,
            model_version   = ai.get("model_version", os.environ.get("SOC_ENRICHMENT_LLM_MODEL", "claude-sonnet-4-6")),
            mitre_technique = ai.get("mitre_technique"),
        )

    return {"ok": True}


def _load_event_row(event_id: str, tenant_id: str) -> dict | None:
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT e.event_id::text, e.tenant_id::text, e.source, e.kind, e.severity, "
            "       e.title, e.actor, e.resource_arn, e.fired_at::text, "
            "       d.before_state, d.after_state "
            "FROM events e LEFT JOIN drift_events d USING (event_id) "
            "WHERE e.event_id = CAST(:e AS UUID) AND e.tenant_id = CAST(:t AS UUID)"
        ),
        parameters=[
            {"name": "e", "value": {"stringValue": event_id}},
            {"name": "t", "value": {"stringValue": tenant_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return None
    r = rows[0]
    cols = ["event_id","tenant_id","source","kind","severity","title","actor","resource_arn","fired_at","before_state","after_state"]
    out: dict = {}
    for col, cell in zip(cols, r):
        if cell.get("isNull"): out[col] = None
        elif "stringValue" in cell:
            out[col] = json.loads(cell["stringValue"]) if col.endswith("_state") and cell["stringValue"] else cell["stringValue"]
        else:
            out[col] = next(iter(cell.values()))
    return out


def _update_event_ai(*, event_id: str, narrative: str | None, anomaly_class: str | None,
                     anomaly_score: int | None, next_steps: list | None,
                     features: dict, model_version: str, mitre_technique: str | None) -> None:
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "UPDATE events SET "
            "  ai_narrative      = :narrative, "
            "  ai_anomaly_class  = :anomaly_class, "
            "  ai_anomaly_score  = :anomaly_score, "
            "  ai_next_steps     = CAST(:next_steps AS JSONB), "
            "  ai_features       = CAST(:features  AS JSONB), "
            "  ai_model_version  = :model_version, "
            "  ai_enriched_at    = now(), "
            "  mitre_technique   = :mitre "
            "WHERE event_id = CAST(:e AS UUID)"
        ),
        parameters=[
            {"name": "e",             "value": {"stringValue": event_id}},
            {"name": "narrative",     "value": ({"stringValue": narrative}      if narrative      else {"isNull": True})},
            {"name": "anomaly_class", "value": ({"stringValue": anomaly_class}  if anomaly_class  else {"isNull": True})},
            {"name": "anomaly_score", "value": ({"longValue":   anomaly_score} if anomaly_score is not None else {"isNull": True})},
            {"name": "next_steps",    "value": ({"stringValue": json.dumps(next_steps)} if next_steps else {"isNull": True})},
            {"name": "features",      "value": {"stringValue": json.dumps(features)}},
            {"name": "model_version", "value": {"stringValue": model_version}},
            {"name": "mitre",         "value": ({"stringValue": mitre_technique} if mitre_technique else {"isNull": True})},
        ],
    )
```

Run tests: `cd platform/lambda/soc_enrichment && python -m pytest tests/ -v`
Expected: 2 passed.

- [ ] **Step 4: CDK — add the Lambda + SQS event source**

In `platform/lib/events-stack.ts`, after the SQS queue block, add:

```typescript
    // ============================================================
    // SOC enrichment Lambda — consumes the queue
    // ============================================================
    const enrichmentFn = new lambda.Function(this, 'SocEnrichmentFn', {
      runtime:    lambda.Runtime.PYTHON_3_12,
      handler:    'main.handler',
      code:       lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'soc_enrichment', 'dist', 'soc_enrichment.zip')),
      timeout:    cdk.Duration.seconds(90),
      memorySize: 1024,
      environment: {
        DB_CLUSTER_ARN:    props.dbCluster.clusterArn,
        DB_SECRET_ARN:     props.dbCluster.secret!.secretArn,
        DB_NAME:           'ciso_copilot',
        SPEND_CAP_TABLE_NAME: this.spendCapTable.tableName,
        SOC_ENRICHMENT_LLM_MODEL: 'claude-sonnet-4-6',
        // ANTHROPIC_API_KEY pulled from Secrets Manager at cold start (see Task 10)
        ANTHROPIC_API_KEY_SECRET_NAME: 'ciso-copilot/anthropic-api-key',
      },
    });
    props.dbCluster.grantDataApiAccess(enrichmentFn);
    this.spendCapTable.grantReadWriteData(enrichmentFn);
    enrichmentFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/anthropic-api-key*`],
    }));
    enrichmentFn.addEventSource(new sqsEventSource.SqsEventSource(this.enrichmentQueue, {
      batchSize: 5,
      maxBatchingWindow: cdk.Duration.seconds(2),
      reportBatchItemFailures: true,
    }));
    new cdk.CfnOutput(this, 'SocEnrichmentFnName', { value: enrichmentFn.functionName });
```

- [ ] **Step 5: Build + deploy**

```bash
cd platform/lambda/soc_enrichment && ./build.sh
cd ../../.. && npx cdk deploy CisoCopilotEvents --require-approval never
```

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/soc_enrichment/ platform/lib/events-stack.ts
git commit -m "feat(soc-s1): soc_enrichment Lambda scaffold + SQS trigger + handler skeleton"
```

---

## Task 9: Statistical features for enrichment

**Files:**
- Create: `platform/lambda/soc_enrichment/features.py`
- Create: `platform/lambda/soc_enrichment/tests/test_features.py`

**Context:** Spec §4.2 — the "hybrid stats → LLM" engine. Cheap features computed in Python from a 30-day events history window, fed to the LLM as structured features. Four features in v1:
- `first_time_actor_on_resource` — boolean
- `off_hours` — boolean, tenant-tz-aware (v1 hardcodes UTC; per-tenant tz is a future enhancement)
- `action_rarity` — `'common' | 'rare' | 'first_time'` based on 30d frequency for this tenant
- `blast_radius_proxy` — small int (number of resources reachable; v1 = number of distinct resource_arns the actor has touched in 30d)

- [ ] **Step 1: Failing tests**

Create `platform/lambda/soc_enrichment/tests/test_features.py`:

```python
import features


def test_off_hours_evening_utc():
    # 22:00 UTC is off-hours by the default 09-18 weekday window
    assert features._is_off_hours("2026-05-25T22:00:00Z") is True


def test_off_hours_business_hours():
    # 14:00 UTC weekday — business hours
    assert features._is_off_hours("2026-05-25T14:00:00Z") is False


def test_off_hours_weekend():
    # 14:00 Sunday — off hours regardless
    assert features._is_off_hours("2026-05-24T14:00:00Z") is True


def test_compute_features_packages_all_signals(monkeypatch):
    monkeypatch.setattr(features, "_first_time_actor_on_resource",
                        lambda tenant_id, actor, resource_arn: True)
    monkeypatch.setattr(features, "_action_rarity",
                        lambda tenant_id, action: "rare")
    monkeypatch.setattr(features, "_blast_radius_proxy",
                        lambda tenant_id, actor: 14)
    row = {
        "tenant_id": "t1", "actor": "user/x", "resource_arn": "sg-abc",
        "title": "AuthorizeSecurityGroupIngress", "fired_at": "2026-05-25T22:00:00Z",
    }
    f = features.compute_features(row)
    assert f == {
        "first_time_actor_on_resource": True,
        "off_hours":                    True,
        "action_rarity":                "rare",
        "blast_radius_proxy":           14,
    }
```

Run: `cd platform/lambda/soc_enrichment && python -m pytest tests/test_features.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 2: Implement features.py**

Create `platform/lambda/soc_enrichment/features.py`:

```python
"""Statistical features for SOC enrichment. All read from Aurora events history."""
from __future__ import annotations
import os
from datetime import datetime
import boto3

DB_CLUSTER_ARN = os.environ.get("DB_CLUSTER_ARN", "")
DB_SECRET_ARN  = os.environ.get("DB_SECRET_ARN", "")
DB_NAME        = os.environ.get("DB_NAME", "ciso_copilot")

rds_data = boto3.client("rds-data")

# Business hours: Mon-Fri 09:00-18:00 UTC. Per-tenant tz is a future enhancement.
BIZ_START = 9
BIZ_END   = 18


def _is_off_hours(fired_at_iso: str) -> bool:
    t = datetime.fromisoformat(fired_at_iso.replace("Z", "+00:00"))
    if t.weekday() >= 5:        # 5,6 = Sat,Sun
        return True
    return not (BIZ_START <= t.hour < BIZ_END)


def _first_time_actor_on_resource(tenant_id: str, actor: str | None, resource_arn: str | None) -> bool:
    if not actor or not resource_arn:
        return False
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT 1 FROM events "
            "WHERE tenant_id = CAST(:t AS UUID) AND actor = :a AND resource_arn = :r "
            "  AND fired_at > now() - interval '30 days' "
            "LIMIT 1"
        ),
        parameters=[
            {"name": "t", "value": {"stringValue": tenant_id}},
            {"name": "a", "value": {"stringValue": actor}},
            {"name": "r", "value": {"stringValue": resource_arn}},
        ],
    )
    return len(rs.get("records", [])) == 0


def _action_rarity(tenant_id: str, action: str | None) -> str:
    if not action:
        return "common"
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT count(*) FROM events "
            "WHERE tenant_id = CAST(:t AS UUID) AND title = :a "
            "  AND fired_at > now() - interval '30 days'"
        ),
        parameters=[
            {"name": "t", "value": {"stringValue": tenant_id}},
            {"name": "a", "value": {"stringValue": action}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return "common"
    n = rows[0][0].get("longValue", 0)
    if n == 0: return "first_time"
    if n < 5:  return "rare"
    return "common"


def _blast_radius_proxy(tenant_id: str, actor: str | None) -> int:
    if not actor:
        return 0
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT count(DISTINCT resource_arn) FROM events "
            "WHERE tenant_id = CAST(:t AS UUID) AND actor = :a "
            "  AND fired_at > now() - interval '30 days' AND resource_arn IS NOT NULL"
        ),
        parameters=[
            {"name": "t", "value": {"stringValue": tenant_id}},
            {"name": "a", "value": {"stringValue": actor}},
        ],
    )
    rows = rs.get("records", [])
    return rows[0][0].get("longValue", 0) if rows else 0


def compute_features(row: dict) -> dict:
    return {
        "first_time_actor_on_resource": _first_time_actor_on_resource(
            row["tenant_id"], row.get("actor"), row.get("resource_arn")),
        "off_hours":                    _is_off_hours(row["fired_at"]),
        "action_rarity":                _action_rarity(row["tenant_id"], row.get("title")),
        "blast_radius_proxy":           _blast_radius_proxy(row["tenant_id"], row.get("actor")),
    }
```

Wire it into `main.py` — replace the stub `compute_features` with `from features import compute_features`.

Run: `cd platform/lambda/soc_enrichment && python -m pytest tests/ -v`
Expected: 6 passed (2 prior + 4 new).

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/soc_enrichment/features.py platform/lambda/soc_enrichment/tests/test_features.py platform/lambda/soc_enrichment/main.py
git commit -m "feat(soc-s1): statistical features (first-time-actor, off-hours, action-rarity, blast-radius proxy)"
```

---

## Task 10: LiteLLM wrapper + prompt template + spend cap

**Files:**
- Create: `platform/lambda/soc_enrichment/llm.py`
- Create: `platform/lambda/soc_enrichment/tests/test_llm.py`
- Modify: `platform/lambda/soc_enrichment/main.py` — wire `call_llm` to real impl

**Context:** Spec §5.1 — LiteLLM as the unified client. Env var `SOC_ENRICHMENT_LLM_MODEL` controls the model (default `claude-sonnet-4-6`). Per-tenant daily spend cap enforced via `spend_cap.llm_spend_*` from Task 6. Prompt template is provider-neutral (no Anthropic-only tool use); requests JSON output via `response_format`. The Anthropic API key is pulled from Secrets Manager at cold start.

- [ ] **Step 1: Failing tests for prompt + cap**

Create `platform/lambda/soc_enrichment/tests/test_llm.py`:

```python
import json
import llm


def test_build_prompt_includes_event_and_features():
    row = {"source": "aws.config", "kind": "drift", "severity": "high",
           "title": "AuthorizeSecurityGroupIngress",
           "actor": "arn:aws:iam::1:user/x",
           "resource_arn": "arn:aws:ec2:us-east-1:1:security-group/sg-abc",
           "fired_at": "2026-05-25T18:42:10Z",
           "after_state": {"ipPermissions": [{"fromPort": 22, "toPort": 22,
                                              "ipRanges": [{"cidrIp": "0.0.0.0/0"}]}]}}
    features = {"first_time_actor_on_resource": True, "off_hours": True,
                "action_rarity": "rare", "blast_radius_proxy": 14}
    msgs = llm.build_messages(row, features)

    # The structure is a system + user pair; user must contain event + features.
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    content = msgs[1]["content"]
    assert "AuthorizeSecurityGroupIngress" in content
    assert "first_time_actor_on_resource" in content
    assert "0.0.0.0/0" in content
    assert "respond with JSON" in msgs[0]["content"].lower()


def test_call_llm_short_circuits_when_cap_reached(monkeypatch):
    monkeypatch.setattr(llm.spend_cap, "llm_spend_today_cents", lambda t: 9999)
    monkeypatch.setattr(llm, "DAILY_CAP_CENTS_DEFAULT", 1000)
    out = llm.call_llm({"tenant_id": "t1"}, {})
    assert out["model_version"] == "cap_reached"
    assert out["narrative"] is None


def test_call_llm_returns_parsed_response(monkeypatch):
    monkeypatch.setattr(llm.spend_cap, "llm_spend_today_cents", lambda t: 0)
    monkeypatch.setattr(llm.spend_cap, "llm_spend_add",         lambda t, c: 0)

    class FakeResp:
        def __init__(self):
            self.choices = [type("C", (), {"message": type("M", (), {
                "content": json.dumps({"narrative": "n", "anomaly_class": "unusual",
                                       "anomaly_score": 60, "next_steps": [],
                                       "mitre_technique": "T1098"})})})]
            self.usage = type("U", (), {"prompt_tokens": 100, "completion_tokens": 50})

    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: FakeResp())
    row = {"tenant_id": "t1", "source": "aws.config", "kind": "drift",
           "severity": "high", "title": "x", "actor": "u", "resource_arn": "r",
           "fired_at": "2026-05-25T00:00:00Z", "after_state": {}}
    out = llm.call_llm(row, {})
    assert out["narrative"] == "n"
    assert out["anomaly_class"] == "unusual"
    assert out["mitre_technique"] == "T1098"
```

Run: `cd platform/lambda/soc_enrichment && python -m pytest tests/test_llm.py -v`
Expected: `ModuleNotFoundError: No module named 'llm'`.

- [ ] **Step 2: Implement llm.py**

Create `platform/lambda/soc_enrichment/llm.py`:

```python
"""LiteLLM wrapper. Model is config-driven via SOC_ENRICHMENT_LLM_MODEL."""
from __future__ import annotations
import json
import os
import sys

import boto3
import litellm

# Import sibling — spend_cap lives next to event_router by default;
# we vendor the file at build time, or sys-path-extend at deploy.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spend_cap  # type: ignore


MODEL = os.environ.get("SOC_ENRICHMENT_LLM_MODEL", "claude-sonnet-4-6")
DAILY_CAP_CENTS_DEFAULT = int(os.environ.get("SOC_ENRICHMENT_DAILY_CAP_CENTS", "1000"))  # $10/day

# Anthropic per-million-token pricing for cost estimation (sonnet 4.6 baseline)
# Format: {model: (input_per_M_cents, output_per_M_cents)}
PRICING = {
    "claude-sonnet-4-6":  (300, 1500),
    "claude-haiku-4-5":   (100,  500),
    "gpt-4o-mini":         (15,   60),
}


SYSTEM = (
    "You are a SOC analyst summarizing a single AWS configuration drift event "
    "for a CISO. Be terse. Be specific. Use the structured features. "
    "Respond with JSON matching this schema exactly: "
    '{"narrative": str (<=240 chars), '
    ' "anomaly_class": "expected"|"unusual"|"suspicious", '
    ' "anomaly_score": int 0-100, '
    ' "next_steps": [{"step": str, "command": str|null}, ... at most 3], '
    ' "mitre_technique": "T1098" (or other MITRE ATT&CK ID) or null}'
)


def _anthropic_key() -> str:
    """Resolve the Anthropic key once per cold start from Secrets Manager."""
    cached = getattr(_anthropic_key, "_cached", None)
    if cached:
        return cached
    name = os.environ.get("ANTHROPIC_API_KEY_SECRET_NAME")
    if not name:
        return os.environ.get("ANTHROPIC_API_KEY", "")
    sm = boto3.client("secretsmanager")
    secret = sm.get_secret_value(SecretId=name)["SecretString"]
    try:
        key = json.loads(secret).get("ANTHROPIC_API_KEY", secret)
    except json.JSONDecodeError:
        key = secret
    _anthropic_key._cached = key  # type: ignore
    return key


def build_messages(row: dict, features: dict) -> list[dict]:
    user_payload = {
        "event": {k: row.get(k) for k in ("source","kind","severity","title","actor","resource_arn","fired_at","after_state","before_state")},
        "features": features,
    }
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": json.dumps(user_payload, default=str)},
    ]


def _estimate_cents(prompt_tokens: int, completion_tokens: int, model: str) -> int:
    in_per_M, out_per_M = PRICING.get(model, (300, 1500))
    return (prompt_tokens * in_per_M // 1_000_000) + (completion_tokens * out_per_M // 1_000_000)


def call_llm(row: dict, features: dict) -> dict:
    tenant_id = row["tenant_id"]
    if spend_cap.llm_spend_today_cents(tenant_id) >= DAILY_CAP_CENTS_DEFAULT:
        return {"narrative": None, "anomaly_class": None, "anomaly_score": None,
                "next_steps": None, "mitre_technique": None, "model_version": "cap_reached"}

    if "anthropic" in MODEL or MODEL.startswith("claude-"):
        os.environ.setdefault("ANTHROPIC_API_KEY", _anthropic_key())

    resp = litellm.completion(
        model=MODEL,
        messages=build_messages(row, features),
        response_format={"type": "json_object"},
        timeout=30,
    )
    raw = resp.choices[0].message.content
    parsed = json.loads(raw) if isinstance(raw, str) else raw

    cents = _estimate_cents(resp.usage.prompt_tokens, resp.usage.completion_tokens, MODEL)
    spend_cap.llm_spend_add(tenant_id, cents)

    return {
        "narrative":       parsed.get("narrative"),
        "anomaly_class":   parsed.get("anomaly_class"),
        "anomaly_score":   parsed.get("anomaly_score"),
        "next_steps":      parsed.get("next_steps"),
        "mitre_technique": parsed.get("mitre_technique"),
        "model_version":   MODEL,
    }
```

- [ ] **Step 3: Vendor spend_cap.py into soc_enrichment build**

`spend_cap.py` lives in `platform/lambda/event_router/`. The simplest fix is to copy it into the soc_enrichment build. Update `platform/lambda/soc_enrichment/build.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf build dist && mkdir -p build dist
pip install --target build -r requirements.txt --quiet
cp -r main.py features.py llm.py parser.py build/
cp ../event_router/spend_cap.py build/
cd build && zip -qr ../dist/soc_enrichment.zip . && cd ..
echo "Built $(pwd)/dist/soc_enrichment.zip"
```

(Future cleanup: lift spend_cap into a `platform/lambda/_shared/` package. Out of scope for Slice 1.)

For local tests, symlink:
```bash
ln -sf ../../event_router/spend_cap.py platform/lambda/soc_enrichment/spend_cap.py
```

- [ ] **Step 4: Wire call_llm into main.py**

In `platform/lambda/soc_enrichment/main.py`, replace the stub `call_llm` with:

```python
from llm import call_llm  # noqa: F401  (re-exported so tests can monkeypatch via main.call_llm)
```

Run all tests: `cd platform/lambda/soc_enrichment && python -m pytest tests/ -v`
Expected: 9 passed (2 main + 4 features + 3 llm).

- [ ] **Step 5: Build + deploy**

```bash
cd platform/lambda/soc_enrichment && ./build.sh
cd ../../.. && npx cdk deploy CisoCopilotEvents --require-approval never
```

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/soc_enrichment/llm.py platform/lambda/soc_enrichment/tests/test_llm.py platform/lambda/soc_enrichment/build.sh platform/lambda/soc_enrichment/main.py platform/lambda/soc_enrichment/spend_cap.py
git commit -m "feat(soc-s1): LiteLLM wrapper + prompt template + per-tenant daily spend cap"
```

---

## Task 11: Extend `events_list` to project AI fields + add detail endpoint

**Files:**
- Modify: `platform/lambda/events_list/main.py` — extend SELECT to include AI columns; add detail handler dispatched by path
- Create: `platform/lambda/events_list/tests/__init__.py`
- Create: `platform/lambda/events_list/tests/test_list.py`
- Create: `platform/lambda/events_list/tests/test_detail.py`
- Modify: `platform/lib/api-stack.ts` — add `GET /events/{event_id}` route bound to the same Lambda

**Context:** Spec §5 — `/soc` web page reads from the existing `events_list` API but needs AI fields surfaced and a detail endpoint that returns the full row + related findings. Adding two new endpoints to the same Lambda (multi-method routing on `event["resource"]`) is simpler than spinning up a new Lambda.

- [ ] **Step 1: Failing tests for AI-field projection**

Create `platform/lambda/events_list/tests/__init__.py` (empty).

Create `platform/lambda/events_list/tests/test_list.py`:

```python
"""GET /events response includes AI fields."""
import json
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main


def test_list_response_includes_ai_fields(monkeypatch):
    """Each event row in the list response carries ai_narrative + ai_anomaly_class."""
    def fake_query(**kw):
        return {"records": [[
            {"stringValue": "11111111-1111-1111-1111-111111111111"},
            {"stringValue": "drift"}, {"stringValue": "aws.config"},
            {"stringValue": "high"},  {"stringValue": "SG opened"},
            {"isNull": True},          # description
            {"stringValue": "sg-abc"},
            {"stringValue": "user/x"},
            {"stringValue": "2026-05-25T18:42:10Z"},
            {"stringValue": "2026-05-25T18:42:12Z"},
            {"stringValue": "Suspicious change to public SG."},   # ai_narrative
            {"stringValue": "suspicious"},                         # ai_anomaly_class
            {"longValue":   88},                                   # ai_anomaly_score
        ]]}
    monkeypatch.setattr(main.rds_data, "execute_statement", fake_query)
    monkeypatch.setattr(main, "_resolve_tenant_id", lambda e: "t1")
    monkeypatch.setattr(main, "_count_total", lambda **kw: 1)

    resp = main.handler({"resource": "/events", "queryStringParameters": {}}, None)
    body = json.loads(resp["body"])
    assert body["total"] == 1
    e = body["events"][0]
    assert e["ai_narrative"] == "Suspicious change to public SG."
    assert e["ai_anomaly_class"] == "suspicious"
    assert e["ai_anomaly_score"] == 88
```

Run: `cd platform/lambda/events_list && python -m pytest tests/test_list.py -v`
Expected: FAIL (the SELECT doesn't include AI columns yet).

- [ ] **Step 2: Extend SELECT in main.py**

In `platform/lambda/events_list/main.py`, change the SQL (around line 48-57):

```python
    sql = (
        "SELECT event_id::text, kind, source, severity, title, description, "
        "       resource_arn, actor, fired_at::text, ingested_at::text, "
        "       ai_narrative, ai_anomaly_class, ai_anomaly_score "
        "FROM events "
        "WHERE tenant_id = CAST(:tid AS UUID) "
        f"  AND severity IN ({_in_clause('sev', severities)}) "
        f"  AND kind     IN ({_in_clause('k',   kinds)}) "
        + (f"  AND source IN ({_in_clause('src', sources)}) " if sources else "")
        + "ORDER BY fired_at DESC LIMIT :limit OFFSET :offset"
    )
```

And update the row-to-dict assembly (find the loop that builds each event from the result) to include the three new fields. Locate the existing assembly block and add:

```python
            "ai_narrative":     _str_or_none(r[10]),
            "ai_anomaly_class": _str_or_none(r[11]),
            "ai_anomaly_score": _int_or_none(r[12]),
```

Where `_str_or_none` and `_int_or_none` are tiny helpers — add them next to the existing parsing helpers:

```python
def _str_or_none(cell): return cell.get("stringValue") if not cell.get("isNull") else None
def _int_or_none(cell): return cell.get("longValue")   if not cell.get("isNull") else None
```

Refactor the row-build to use them where it makes sense.

Re-run: `python -m pytest tests/test_list.py -v`
Expected: PASS.

- [ ] **Step 3: Failing test for detail endpoint**

Create `platform/lambda/events_list/tests/test_detail.py`:

```python
import json
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main


def test_detail_returns_full_row_plus_related_findings(monkeypatch):
    monkeypatch.setattr(main, "_resolve_tenant_id", lambda e: "t1")

    def fake_query(**kw):
        sql = kw["sql"]
        if "FROM events e" in sql:
            return {"records": [[
                {"stringValue": "11111111-1111-1111-1111-111111111111"},
                {"stringValue": "drift"}, {"stringValue": "aws.config"},
                {"stringValue": "high"},  {"stringValue": "SG opened"},
                {"isNull": True},  {"stringValue": "sg-abc"}, {"stringValue": "user/x"},
                {"stringValue": "2026-05-25T18:42:10Z"}, {"stringValue": "2026-05-25T18:42:12Z"},
                {"stringValue": "n"}, {"stringValue": "suspicious"}, {"longValue": 88},
                {"stringValue": '[{"step":"x","command":"y"}]'},  # ai_next_steps JSON
                {"stringValue": '{"off_hours":true}'},            # ai_features JSON
                {"stringValue": "claude-sonnet-4-6"},
                {"stringValue": "T1098"},
                {"stringValue": "AuthorizeSecurityGroupIngress"},
                {"stringValue": '{"ipPermissions":[]}'},          # after_state
                {"isNull": True},                                  # before_state
            ]]}
        if "FROM findings" in sql:
            return {"records": [[
                {"stringValue": "ec2-22-open-world"},
                {"stringValue": "Security group open to world on SSH"},
                {"stringValue": "high"},
            ]]}
        return {"records": []}

    monkeypatch.setattr(main.rds_data, "execute_statement", fake_query)

    resp = main.handler({
        "resource": "/events/{event_id}",
        "pathParameters": {"event_id": "11111111-1111-1111-1111-111111111111"},
    }, None)
    body = json.loads(resp["body"])
    assert body["event"]["ai_narrative"] == "n"
    assert body["event"]["ai_next_steps"] == [{"step": "x", "command": "y"}]
    assert body["event"]["action"] == "AuthorizeSecurityGroupIngress"
    assert len(body["related_findings"]) == 1
    assert body["related_findings"][0]["check_id"] == "ec2-22-open-world"
```

Run: expected FAIL (handler doesn't route on `/events/{event_id}` yet).

- [ ] **Step 4: Add path routing + detail handler**

At the top of `handler` in `main.py`:

```python
    resource = event.get("resource", "")
    if resource == "/events":
        return _list_handler(event, context)
    if resource == "/events/{event_id}":
        return _detail_handler(event, context)
    if resource == "/events/{event_id}/feedback":
        return _feedback_handler(event, context)
    return _resp(404, {"error": "not_found"})
```

Rename the current handler body into `_list_handler`. Add `_detail_handler`:

```python
def _detail_handler(event: dict, context) -> dict:
    tenant_id = _resolve_tenant_id(event)
    if not tenant_id: return _resp(401, {"error": "no_tenant"})
    event_id = (event.get("pathParameters") or {}).get("event_id")
    if not event_id: return _resp(400, {"error": "missing_event_id"})

    row = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "SELECT e.event_id::text, e.kind, e.source, e.severity, e.title, e.description, "
            "       e.resource_arn, e.actor, e.fired_at::text, e.ingested_at::text, "
            "       e.ai_narrative, e.ai_anomaly_class, e.ai_anomaly_score, "
            "       e.ai_next_steps::text, e.ai_features::text, e.ai_model_version, "
            "       e.mitre_technique, d.action, d.after_state::text, d.before_state::text "
            "FROM events e LEFT JOIN drift_events d USING (event_id) "
            "WHERE e.event_id = CAST(:e AS UUID) AND e.tenant_id = CAST(:t AS UUID)"
        ),
        parameters=[
            {"name": "e", "value": {"stringValue": event_id}},
            {"name": "t", "value": {"stringValue": tenant_id}},
        ],
    ).get("records", [])
    if not row: return _resp(404, {"error": "not_found"})
    r = row[0]
    evt = {
        "event_id":         r[0]["stringValue"],
        "kind":             r[1]["stringValue"],
        "source":           r[2]["stringValue"],
        "severity":         r[3]["stringValue"],
        "title":            r[4]["stringValue"],
        "description":      _str_or_none(r[5]),
        "resource_arn":     _str_or_none(r[6]),
        "actor":            _str_or_none(r[7]),
        "fired_at":         r[8]["stringValue"],
        "ingested_at":      r[9]["stringValue"],
        "ai_narrative":     _str_or_none(r[10]),
        "ai_anomaly_class": _str_or_none(r[11]),
        "ai_anomaly_score": _int_or_none(r[12]),
        "ai_next_steps":    json.loads(r[13]["stringValue"]) if not r[13].get("isNull") else None,
        "ai_features":      json.loads(r[14]["stringValue"]) if not r[14].get("isNull") else None,
        "ai_model_version": _str_or_none(r[15]),
        "mitre_technique":  _str_or_none(r[16]),
        "action":           _str_or_none(r[17]),
        "after_state":      json.loads(r[18]["stringValue"]) if not r[18].get("isNull") else None,
        "before_state":     json.loads(r[19]["stringValue"]) if not r[19].get("isNull") else None,
    }

    related = []
    if evt["resource_arn"]:
        rs = rds_data.execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=(
                "SELECT check_id, title, severity FROM findings "
                "WHERE tenant_id = CAST(:t AS UUID) AND resource_arn = :r "
                "  AND status = 'fail' ORDER BY severity LIMIT 10"
            ),
            parameters=[
                {"name": "t", "value": {"stringValue": tenant_id}},
                {"name": "r", "value": {"stringValue": evt["resource_arn"]}},
            ],
        )
        for fr in rs.get("records", []):
            related.append({
                "check_id": fr[0]["stringValue"],
                "title":    fr[1]["stringValue"],
                "severity": fr[2]["stringValue"],
            })

    return _resp(200, {"event": evt, "related_findings": related})
```

Add `_feedback_handler` stub (filled in Task 12):

```python
def _feedback_handler(event: dict, context) -> dict:
    return _resp(501, {"error": "not_implemented"})
```

Run both tests: `python -m pytest tests/ -v`
Expected: all 2 list+detail tests pass.

- [ ] **Step 5: Wire detail route in api-stack.ts**

In `platform/lib/api-stack.ts`, find the existing `api.root.addResource('events').addMethod(...)` block (around line 496) and append:

```typescript
    const eventsResource = api.root.getResource('events')!;
    const eventIdResource = eventsResource.addResource('{event_id}');
    eventIdResource.addMethod('GET',
      new apigw.LambdaIntegration(eventsListFn),
      { authorizer: cognitoAuthorizer, authorizationType: apigw.AuthorizationType.COGNITO });
```

(Variable names may differ — use whatever the existing block uses for `eventsListFn` and the authorizer.)

- [ ] **Step 6: Deploy + commit**

```bash
cd platform && npx cdk deploy CisoCopilotApi --require-approval never --hotswap
git add platform/lambda/events_list/ platform/lib/api-stack.ts
git commit -m "feat(soc-s1): events_list projects AI fields + GET /events/{event_id} detail"
```

---

## Task 12: POST `/events/{event_id}/feedback` endpoint

**Files:**
- Modify: `platform/lambda/events_list/main.py` — implement `_feedback_handler`
- Create: `platform/lambda/events_list/tests/test_feedback.py`
- Modify: `platform/lib/api-stack.ts` — wire POST route

**Context:** Existing `feedback` table (`002_phase_a.sql:144`) has columns `(feedback_id, tenant_id, user_id, target_kind, target_id, sentiment, reason)`. We write `target_kind='event'`. Spec §5 component table line on `feedback`.

- [ ] **Step 1: Failing test**

Create `platform/lambda/events_list/tests/test_feedback.py`:

```python
import json
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main


def test_feedback_writes_to_feedback_table(monkeypatch):
    monkeypatch.setattr(main, "_resolve_tenant_id", lambda e: "t1")
    monkeypatch.setattr(main, "_resolve_user_id",  lambda e: "u1")

    calls = []
    def fake_exec(**kw):
        calls.append(kw["sql"])
        return {"records": []}
    monkeypatch.setattr(main.rds_data, "execute_statement", fake_exec)

    resp = main.handler({
        "resource": "/events/{event_id}/feedback",
        "httpMethod": "POST",
        "pathParameters": {"event_id": "11111111-1111-1111-1111-111111111111"},
        "body": json.dumps({"sentiment": "up", "reason": "useful narrative"}),
    }, None)

    assert resp["statusCode"] == 200
    assert any("INSERT INTO feedback" in s for s in calls)
```

Run: expected FAIL (stub returns 501).

- [ ] **Step 2: Implement `_feedback_handler`**

Replace the stub in `main.py`:

```python
def _resolve_user_id(event: dict) -> str | None:
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims", {})
    return claims.get("custom:user_id") or claims.get("sub")


def _feedback_handler(event: dict, context) -> dict:
    if event.get("httpMethod") != "POST":
        return _resp(405, {"error": "method_not_allowed"})
    tenant_id = _resolve_tenant_id(event)
    user_id   = _resolve_user_id(event)
    if not tenant_id or not user_id:
        return _resp(401, {"error": "no_tenant_or_user"})

    event_id = (event.get("pathParameters") or {}).get("event_id")
    body     = json.loads(event.get("body") or "{}")
    sentiment = body.get("sentiment")
    reason    = body.get("reason")

    if sentiment not in ("up", "down"):
        return _resp(400, {"error": "invalid_sentiment"})

    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "INSERT INTO feedback (feedback_id, tenant_id, user_id, target_kind, target_id, sentiment, reason) "
            "VALUES (gen_random_uuid(), CAST(:t AS UUID), CAST(:u AS UUID), 'event', "
            "        CAST(:id AS UUID), :s, :r)"
        ),
        parameters=[
            {"name": "t",  "value": {"stringValue": tenant_id}},
            {"name": "u",  "value": {"stringValue": user_id}},
            {"name": "id", "value": {"stringValue": event_id}},
            {"name": "s",  "value": {"stringValue": sentiment}},
            {"name": "r",  "value": ({"stringValue": reason} if reason else {"isNull": True})},
        ],
    )
    return _resp(200, {"ok": True})
```

Run test: PASS.

- [ ] **Step 3: Wire POST route in api-stack.ts**

```typescript
    const feedbackResource = eventIdResource.addResource('feedback');
    feedbackResource.addMethod('POST',
      new apigw.LambdaIntegration(eventsListFn),
      { authorizer: cognitoAuthorizer, authorizationType: apigw.AuthorizationType.COGNITO });
```

- [ ] **Step 4: Deploy + commit**

```bash
cd platform && npx cdk deploy CisoCopilotApi --require-approval never --hotswap
git add platform/lambda/events_list/ platform/lib/api-stack.ts
git commit -m "feat(soc-s1): POST /events/{event_id}/feedback writes to feedback table"
```

---

## Task 13: Refine AWS Config recording to essentials profile (cost reduction)

**Files:**
- Modify: `platform/cfn/aws-onboard.yaml` — add `RecordingMode` parameter; default `essentials`

**Context:** Spec §10.1 commits to ~$30-80/mo customer-side cost via the essentials profile. The current CFN (`aws-onboard.yaml:236-238`) records `AllSupported: true` which is ~3-10x more expensive in busy accounts. We add a `RecordingMode` parameter so customers can opt into full recording if they want; default is essentials with an explicit ~25-resource-type list of security-critical types.

- [ ] **Step 1: Add parameter + Conditions**

In `platform/cfn/aws-onboard.yaml`, in the `Parameters` block add:

```yaml
  ConfigRecordingMode:
    Type: String
    Default: essentials
    AllowedValues: [essentials, all]
    Description: >
      'essentials' records ~25 security-critical resource types (~$30-80/mo
      in a typical account). 'all' records everything (~3-10x more expensive,
      mostly unused signal for SOC). Default essentials.
```

In `Conditions` add:

```yaml
  RecordEssentials: !And
    - !Condition ShouldEnableConfig
    - !Equals [!Ref ConfigRecordingMode, "essentials"]
  RecordAll: !And
    - !Condition ShouldEnableConfig
    - !Equals [!Ref ConfigRecordingMode, "all"]
```

- [ ] **Step 2: Split `ConfigRecorder` into two conditional resources**

Replace the existing `ConfigRecorder` block (lines ~229-238) with:

```yaml
  ConfigRecorderEssentials:
    Type: AWS::Config::ConfigurationRecorder
    Condition: RecordEssentials
    DependsOn: ConfigDeliveryChannel
    Properties:
      Name: CISOCopilotConfigRecorder
      RoleARN: !GetAtt ConfigRecorderRole.Arn
      RecordingGroup:
        AllSupported: false
        IncludeGlobalResourceTypes: false
        ResourceTypes:
          # IAM (global)
          - AWS::IAM::User
          - AWS::IAM::Role
          - AWS::IAM::Group
          - AWS::IAM::Policy
          - AWS::IAM::AccessKey
          # Compute
          - AWS::EC2::Instance
          - AWS::EC2::SecurityGroup
          - AWS::EC2::NetworkAcl
          - AWS::EC2::Subnet
          - AWS::EC2::VPC
          - AWS::EC2::RouteTable
          - AWS::EC2::InternetGateway
          - AWS::EC2::NatGateway
          - AWS::EC2::VPCPeeringConnection
          # Lambda
          - AWS::Lambda::Function
          # Storage / Data
          - AWS::S3::Bucket
          - AWS::RDS::DBInstance
          - AWS::RDS::DBCluster
          - AWS::DynamoDB::Table
          - AWS::SecretsManager::Secret
          # Crypto
          - AWS::KMS::Key
          # Audit / Logging
          - AWS::CloudTrail::Trail
          - AWS::Config::ConfigurationRecorder
          # Containers
          - AWS::EKS::Cluster

  ConfigRecorderAll:
    Type: AWS::Config::ConfigurationRecorder
    Condition: RecordAll
    DependsOn: ConfigDeliveryChannel
    Properties:
      Name: CISOCopilotConfigRecorder
      RoleARN: !GetAtt ConfigRecorderRole.Arn
      RecordingGroup:
        AllSupported: true
        IncludeGlobalResourceTypes: true
```

- [ ] **Step 3: Re-validate the template**

```bash
aws cloudformation validate-template --template-body file://platform/cfn/aws-onboard.yaml | head -5
```

Expected: returns `Description` + `Parameters` summary, no error.

- [ ] **Step 4: Smoke-deploy to a test account (optional but recommended)**

If a test customer AWS account is available, deploy the updated template with `ConfigRecordingMode=essentials` and confirm the recorder reports recording only the listed types via:

```bash
aws configservice describe-configuration-recorders --query 'ConfigurationRecorders[0].recordingGroup.resourceTypes'
```

- [ ] **Step 5: Commit**

```bash
git add platform/cfn/aws-onboard.yaml
git commit -m "feat(soc-s1): AWS Config essentials recording profile (~25 security-critical types, cost reduction)"
```

---

## Task 14: `/soc` web page — timeline + filter chips + detail pane + feedback

**Files:**
- Modify: existing route table in `web/src/App.tsx` (or wherever routes are declared) — add `/soc` route
- Create: `web/src/routes/Soc.tsx`
- Create: `web/src/routes/Soc.test.tsx`
- Create: `web/src/components/soc/Timeline.tsx`
- Create: `web/src/components/soc/FilterChips.tsx`
- Create: `web/src/components/soc/DetailPane.tsx`
- Create: `web/src/components/soc/FeedbackButtons.tsx`

**Context:** Spec §5 / §7 — `/soc` shows live timeline (newest-first), filter chips (cloud / source / severity / actor / time-range), detail pane (AI narrative + features + next-steps + related findings + feedback). Uses the existing API client `web/src/lib/api.ts` (check that file for the existing pattern — fetch helpers, types). Reuse existing card / badge components if they exist.

- [ ] **Step 1: Skim existing client + types**

Read `web/src/lib/api.ts` and one existing route (e.g. `web/src/routes/TopRisks.tsx`) to confirm the fetch + Cognito auth pattern. The implementation below assumes a `apiGet(path)` and `apiPost(path, body)` helper exist. If they don't, mirror whatever exists.

- [ ] **Step 2: Types + Timeline component**

Create `web/src/components/soc/Timeline.tsx`:

```tsx
import { Link } from 'react-router-dom';

export type DriftEvent = {
  event_id: string;
  source: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  title: string;
  resource_arn: string | null;
  actor: string | null;
  fired_at: string;
  ai_narrative: string | null;
  ai_anomaly_class: 'expected' | 'unusual' | 'suspicious' | null;
  ai_anomaly_score: number | null;
};

const SEV_CLASS: Record<DriftEvent['severity'], string> = {
  critical: 'bg-red-100 text-red-900 border-red-300',
  high:     'bg-orange-100 text-orange-900 border-orange-300',
  medium:   'bg-yellow-100 text-yellow-900 border-yellow-300',
  low:      'bg-stone-100 text-stone-700 border-stone-300',
  info:     'bg-stone-50  text-stone-600 border-stone-200',
};

export function Timeline({ events, onSelect }: { events: DriftEvent[]; onSelect: (id: string) => void }) {
  if (events.length === 0) {
    return <div className="text-stone-500 p-8 text-center">No drift events yet. They land here within 60 seconds of occurring in connected clouds.</div>;
  }
  return (
    <ul className="divide-y divide-stone-200">
      {events.map(e => (
        <li key={e.event_id}
            onClick={() => onSelect(e.event_id)}
            className="p-4 hover:bg-stone-50 cursor-pointer">
          <div className="flex items-start gap-3">
            <span className={`px-2 py-0.5 text-xs border rounded ${SEV_CLASS[e.severity]}`}>{e.severity}</span>
            <div className="flex-1 min-w-0">
              <div className="font-medium text-stone-900 truncate">{e.title}</div>
              <div className="text-sm text-stone-600 truncate">
                {e.resource_arn?.split('/').pop()} {e.actor && <>· by <span className="font-mono">{e.actor.split('/').pop()}</span></>}
              </div>
              {e.ai_narrative && (
                <div className="text-sm text-stone-700 mt-1 line-clamp-2">{e.ai_narrative}</div>
              )}
              {!e.ai_narrative && (
                <div className="text-xs text-stone-400 mt-1 italic">AI analysis pending…</div>
              )}
            </div>
            <div className="text-xs text-stone-500 whitespace-nowrap">{new Date(e.fired_at).toLocaleString()}</div>
            {e.ai_anomaly_class === 'suspicious' && (
              <span className="px-2 py-0.5 text-xs border border-red-400 text-red-700 rounded">suspicious</span>
            )}
            {e.ai_anomaly_class === 'unusual' && (
              <span className="px-2 py-0.5 text-xs border border-amber-400 text-amber-700 rounded">unusual</span>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 3: FilterChips component**

Create `web/src/components/soc/FilterChips.tsx`:

```tsx
export type Filters = {
  severity: Array<'critical' | 'high' | 'medium' | 'low'>;
  source:   string[];
};

const ALL_SEVS: Filters['severity']  = ['critical', 'high', 'medium', 'low'];
const ALL_SOURCES                    = ['aws.config', 'aws.cloudtrail', 'aws.guardduty', 'aws.inspector2', 'aws.securityhub'];

export function FilterChips({ value, onChange }: { value: Filters; onChange: (f: Filters) => void }) {
  function toggle<T>(arr: T[], item: T): T[] {
    return arr.includes(item) ? arr.filter(x => x !== item) : [...arr, item];
  }
  return (
    <div className="flex flex-wrap gap-2 p-3 border-b border-stone-200">
      <span className="text-xs text-stone-500 self-center">Severity:</span>
      {ALL_SEVS.map(s => (
        <button key={s}
          onClick={() => onChange({ ...value, severity: toggle(value.severity, s) })}
          className={`px-2 py-0.5 text-xs border rounded ${value.severity.includes(s)
            ? 'bg-stone-900 text-white border-stone-900'
            : 'bg-white text-stone-700 border-stone-300'}`}>
          {s}
        </button>
      ))}
      <span className="text-xs text-stone-500 self-center ml-4">Source:</span>
      {ALL_SOURCES.map(s => (
        <button key={s}
          onClick={() => onChange({ ...value, source: toggle(value.source, s) })}
          className={`px-2 py-0.5 text-xs border rounded ${value.source.includes(s)
            ? 'bg-stone-900 text-white border-stone-900'
            : 'bg-white text-stone-700 border-stone-300'}`}>
          {s}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: DetailPane + FeedbackButtons**

Create `web/src/components/soc/FeedbackButtons.tsx`:

```tsx
import { useState } from 'react';
import { apiPost } from '../../lib/api';

export function FeedbackButtons({ eventId }: { eventId: string }) {
  const [sent, setSent] = useState<'up' | 'down' | null>(null);
  async function send(sentiment: 'up' | 'down') {
    await apiPost(`/events/${eventId}/feedback`, { sentiment });
    setSent(sentiment);
  }
  if (sent) return <div className="text-xs text-stone-500">Thanks for the feedback.</div>;
  return (
    <div className="flex gap-2">
      <button onClick={() => send('up')}   className="px-2 py-1 text-xs border border-stone-300 rounded hover:bg-stone-50">👍 helpful</button>
      <button onClick={() => send('down')} className="px-2 py-1 text-xs border border-stone-300 rounded hover:bg-stone-50">👎 not useful</button>
    </div>
  );
}
```

Create `web/src/components/soc/DetailPane.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { apiGet } from '../../lib/api';
import { FeedbackButtons } from './FeedbackButtons';

type Detail = {
  event: {
    event_id: string;
    title: string;
    source: string;
    severity: string;
    resource_arn: string | null;
    actor: string | null;
    fired_at: string;
    ai_narrative: string | null;
    ai_anomaly_class: string | null;
    ai_anomaly_score: number | null;
    ai_next_steps: Array<{ step: string; command: string | null }> | null;
    ai_features: Record<string, unknown> | null;
    ai_model_version: string | null;
    mitre_technique: string | null;
    action: string | null;
    after_state: unknown;
    before_state: unknown;
  };
  related_findings: Array<{ check_id: string; title: string; severity: string }>;
};

export function DetailPane({ eventId, onClose }: { eventId: string; onClose: () => void }) {
  const [data, setData] = useState<Detail | null>(null);
  useEffect(() => {
    setData(null);
    apiGet<Detail>(`/events/${eventId}`).then(setData);
  }, [eventId]);
  if (!data) return <div className="p-6 text-stone-500">Loading…</div>;
  const { event: e, related_findings } = data;
  return (
    <aside className="w-96 border-l border-stone-200 bg-white p-4 overflow-y-auto">
      <div className="flex justify-between items-start mb-3">
        <h3 className="font-medium text-stone-900">{e.title}</h3>
        <button onClick={onClose} className="text-stone-400 hover:text-stone-700">✕</button>
      </div>
      <div className="text-xs text-stone-500 mb-3">
        {e.source} · {new Date(e.fired_at).toLocaleString()} · {e.severity}
        {e.actor && <> · by <span className="font-mono">{e.actor.split('/').pop()}</span></>}
      </div>
      {e.ai_narrative ? (
        <>
          <div className="text-sm text-stone-800 mb-3">{e.ai_narrative}</div>
          {e.ai_anomaly_class && (
            <div className="text-xs mb-3">
              <span className="font-medium">Anomaly:</span> {e.ai_anomaly_class}
              {e.ai_anomaly_score !== null && ` (score ${e.ai_anomaly_score}/100)`}
            </div>
          )}
          {e.ai_next_steps && e.ai_next_steps.length > 0 && (
            <div className="mb-3">
              <div className="text-xs font-medium text-stone-700 mb-1">Suggested next steps</div>
              <ul className="text-sm space-y-1">
                {e.ai_next_steps.map((s, i) => (
                  <li key={i} className="text-stone-700">
                    {s.step}
                    {s.command && <pre className="text-xs bg-stone-100 p-1 mt-1 overflow-x-auto"><code>{s.command}</code></pre>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {e.ai_features && (
            <details className="text-xs text-stone-500 mb-3">
              <summary className="cursor-pointer">Why this fired (features)</summary>
              <pre className="bg-stone-50 p-2 mt-1 overflow-x-auto">{JSON.stringify(e.ai_features, null, 2)}</pre>
            </details>
          )}
        </>
      ) : (
        <div className="text-sm text-stone-500 italic mb-3">AI analysis in progress…</div>
      )}
      {related_findings.length > 0 && (
        <div className="mb-3">
          <div className="text-xs font-medium text-stone-700 mb-1">Related findings on this resource</div>
          <ul className="text-sm space-y-1">
            {related_findings.map(f => (
              <li key={f.check_id} className="text-stone-700">
                <span className="text-xs text-stone-500">[{f.severity}]</span> {f.title}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="mt-4 pt-3 border-t border-stone-200">
        <FeedbackButtons eventId={e.event_id} />
      </div>
      {e.ai_model_version && (
        <div className="text-xs text-stone-400 mt-3">AI: {e.ai_model_version}</div>
      )}
    </aside>
  );
}
```

- [ ] **Step 5: Main `Soc.tsx` page**

Create `web/src/routes/Soc.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { apiGet } from '../lib/api';
import { Timeline, DriftEvent } from '../components/soc/Timeline';
import { FilterChips, Filters } from '../components/soc/FilterChips';
import { DetailPane } from '../components/soc/DetailPane';

export default function Soc() {
  const [filters,  setFilters]  = useState<Filters>({ severity: ['critical', 'high'], source: [] });
  const [events,   setEvents]   = useState<DriftEvent[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading,  setLoading]  = useState(true);

  useEffect(() => {
    setLoading(true);
    const qs = new URLSearchParams({
      kind:     'drift',
      severity: filters.severity.join(','),
      limit:    '50',
    });
    if (filters.source.length) qs.set('source', filters.source.join(','));
    apiGet<{ events: DriftEvent[] }>(`/events?${qs.toString()}`)
      .then(r => setEvents(r.events))
      .finally(() => setLoading(false));
  }, [filters]);

  return (
    <div className="flex h-full">
      <div className="flex-1 flex flex-col">
        <header className="p-4 border-b border-stone-200">
          <h1 className="text-xl font-medium text-stone-900">SOC</h1>
          <p className="text-sm text-stone-500">Live drift + alert feed from your connected clouds. AI-enriched.</p>
        </header>
        <FilterChips value={filters} onChange={setFilters} />
        <div className="flex-1 overflow-y-auto">
          {loading
            ? <div className="p-8 text-stone-500 text-center">Loading…</div>
            : <Timeline events={events} onSelect={setSelected} />}
        </div>
      </div>
      {selected && <DetailPane eventId={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
```

- [ ] **Step 6: Wire the route**

In `web/src/App.tsx` (or wherever routes live — check current routing pattern), add:

```tsx
import Soc from './routes/Soc';
// ... in the routes block:
<Route path="/soc" element={<Soc />} />
```

And add a nav link in whichever component renders the top navigation (e.g., `web/src/routes/Shell.tsx`):

```tsx
<Link to="/soc">SOC</Link>
```

- [ ] **Step 7: Vitest for Soc page**

Create `web/src/routes/Soc.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Soc from './Soc';
import * as api from '../lib/api';

vi.mock('../lib/api');

test('renders empty state when no events', async () => {
  vi.mocked(api.apiGet).mockResolvedValue({ events: [] });
  render(<MemoryRouter><Soc /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText(/No drift events yet/)).toBeInTheDocument());
});

test('renders timeline + AI narrative', async () => {
  vi.mocked(api.apiGet).mockResolvedValue({
    events: [{
      event_id: 'e1', source: 'aws.config', severity: 'high',
      title: 'AuthorizeSecurityGroupIngress', resource_arn: 'arn:aws:ec2:us-east-1:1:security-group/sg-abc',
      actor: 'arn:aws:iam::1:user/x', fired_at: '2026-05-25T18:42:10Z',
      ai_narrative: 'Public ingress added to SSH.', ai_anomaly_class: 'unusual', ai_anomaly_score: 70,
    }],
  });
  render(<MemoryRouter><Soc /></MemoryRouter>);
  await waitFor(() => expect(screen.getByText(/Public ingress added/)).toBeInTheDocument());
  expect(screen.getByText('unusual')).toBeInTheDocument();
});
```

Run: `cd web && pnpm test src/routes/Soc.test.tsx`
Expected: 2 passed.

- [ ] **Step 8: Build + deploy web**

```bash
cd web && pnpm build && aws s3 sync dist/ s3://<WEB_BUCKET>/ --delete
aws cloudfront create-invalidation --distribution-id <CLOUDFRONT_DIST_ID> --paths '/*'
```

- [ ] **Step 9: Commit**

```bash
git add web/src/routes/Soc.tsx web/src/routes/Soc.test.tsx web/src/components/soc/ web/src/App.tsx
git commit -m "feat(soc-s1): /soc web page — timeline + filter chips + detail pane + feedback"
```

---

## Task 15: Customer docs + TEST_PLAN gate + open Slice 1 PR

**Files:**
- Create: `docs/customer/drift-detection-aws.md`
- Modify: `TEST_PLAN.md`
- Modify: `HANDOFF.md` — add a "🚀 SOC Slice 1 — shipped" block

**Context:** Spec §10 commits to per-cloud customer docs. Final step closes the slice.

- [ ] **Step 1: Customer doc**

Create `docs/customer/drift-detection-aws.md`:

```markdown
# AWS Drift Detection — what we install, what it costs

When you enable "Drift detection" on your AWS connection, the
CloudFormation stack we provision in your account adds:

## AWS Config recorder (essentials profile by default)

Records configuration-state changes for ~25 security-critical resource
types — IAM (users, roles, groups, policies, access keys), networking
(security groups, NACLs, VPCs, subnets), compute (EC2, Lambda, EKS),
storage (S3 buckets, RDS instances, DynamoDB tables), crypto (KMS keys),
secrets (Secrets Manager), and audit infrastructure (CloudTrail trails,
Config recorders).

**Customer cost:** ~$30-80/month in a typical mid-size AWS account.
AWS Config charges per configuration item recorded (currently $0.003
per item). The essentials profile keeps cost low; if you want full
all-resources recording, deploy the stack with `ConfigRecordingMode=all`
(typically 3-10x more cost).

**Without this:** drift is detected at posture-scan cadence (daily)
instead of within 60 seconds.

## EventBridge rule

Forwards GuardDuty findings, Inspector findings, Security Hub
aggregated alerts, AWS Config item changes, and specific
security-relevant CloudTrail write events (security group changes,
IAM mutations, MFA changes, S3 bucket policy changes) to our central
event bus.

**Customer cost:** $0. Cross-account `PutEvents` is free; the rule
itself is also free.

## What we do NOT enable

- CloudTrail data events (S3 object-level reads/writes, Lambda
  invocations) — too high volume and cost.
- VPC Flow Logs — too high volume.
- Inline traffic inspection or endpoint agents — wrong product shape.
- AWS Config "all resources" recording — only the essentials list by
  default. Opt in via `ConfigRecordingMode=all` if you want it.

## How to opt out

Re-deploy the CloudFormation stack with `EnableAwsConfig=false`. The
Config recorder, delivery channel, and delivery bucket are dropped.
Existing event history we've already ingested stays queryable in our
backend; new drift events stop landing for that connection.
```

- [ ] **Step 2: TEST_PLAN gate**

Append to `TEST_PLAN.md`:

```markdown
## SOC Slice 1 — AWS Config drift end-to-end (added 2026-05-25)

### Setup (one-time per test session)

- Test AWS account `$AWS_ACCOUNT_ID` already onboarded with `ConfigRecordingMode=essentials` (default in the latest aws-onboard.yaml).
- Test user has `device_token` populated in the `users` table (verify via Aurora query).
- iPhone signed in to CISO Copilot iOS app on TestFlight.

### Gate

1. In the test AWS account, open a security group to the world on port 22:
   ```bash
   aws ec2 authorize-security-group-ingress \
     --group-id sg-TESTGROUP \
     --protocol tcp --port 22 --cidr 0.0.0.0/0
   ```

2. **Within 20s:** Refresh https://$SHASTA_DOMAIN/soc — the event appears at the top of the timeline with severity `high`, source `aws.config`, title `AuthorizeSecurityGroupIngress`, resource shown as `sg-TESTGROUP`, actor shown as the IAM user that ran the command.

3. **Within 60s:** iPhone vibrates with a push notification matching the templated body: `drift · high · sg-TESTGROUP · AuthorizeSecurityGroupIngress · by <user>`.

4. **Within ~25s of the event:** Tap the timeline row in `/soc`. The detail pane shows:
   - AI narrative (1-2 sentences naming what happened and why it's notable)
   - Anomaly class (`unusual` or `suspicious` likely; `expected` if this actor regularly opens SGs)
   - Anomaly score 0-100
   - Suggested next steps (e.g., "Revoke the rule" with the corresponding `aws ec2 revoke-security-group-ingress` command)
   - "Why this fired (features)" expandable block showing `first_time_actor_on_resource`, `off_hours`, `action_rarity`, `blast_radius_proxy`
   - Related findings on the resource (if any)
   - Feedback buttons

5. Click 👍 helpful. Expect "Thanks for the feedback" + new row in `feedback` table with `target_kind='event'`, `sentiment='up'`.

6. **Cleanup:** Revoke the rule:
   ```bash
   aws ec2 revoke-security-group-ingress \
     --group-id sg-TESTGROUP \
     --protocol tcp --port 22 --cidr 0.0.0.0/0
   ```
   This generates a second drift event with severity `medium` (revocation is the safe direction — rule fires `medium` per severity_rules.py). Verify it appears in /soc.

### Failure modes to watch

- AI narrative absent for >30s after event lands → check CloudWatch logs for `/aws/lambda/CisoCopilotEvents-SocEnrichment*` (Anthropic 5xx? cap reached? prompt parse failure?).
- Push doesn't arrive → check `users.device_token` populated; check `events.push_sent=true` for the row; check CloudWatch logs for SNS publish errors.
- Duplicate event row → check `source_event_id` is populated; the ON CONFLICT should have deduped.
```

- [ ] **Step 3: HANDOFF.md status block**

Prepend to `HANDOFF.md` (after the existing top date-stamped block, just before the next ### heading):

```markdown
## 🚀 SOC Slice 1 — shipped (2026-05-25)

AI-powered SOC sub-project Slice 1 live end-to-end. AWS Config drift
flows: customer EventBridge rule → central bus → event_router (with new
source_event_id dedupe + Config severity rule table + push +
SQS enqueue) → soc-enrichment-queue → soc_enrichment Lambda
(features + LiteLLM `claude-sonnet-4-6` + UPDATE) → /soc web page +
APNs push.

**What's live:**
- New `/soc` page (web) — timeline + filter chips + detail pane + feedback
- New `soc_enrichment` Lambda with LiteLLM abstraction (env-driven model)
- Per-tenant daily LLM spend cap ($10 default) in DynamoDB
- Per-tenant push rate limit (10/hr default, criticals bypass)
- Schema migration 005 — AI fields + source_event_id + indices + drift target_resource_arn
- AWS Config essentials recording profile (~$30-80/mo customer cost vs $200+ for all-resources)

**Next slice:** 1c — pluggable threat-intel substrate (free feeds: abuse.ch, CISA KEV, Tor list, GreyNoise Community). Then Slice 2 — identity drift (AWS IAM via CloudTrail + Entra audit).

**Spec:** `docs/superpowers/specs/2026-05-25-ai-powered-soc-design.md`
**Plan:** `docs/superpowers/plans/2026-05-25-ai-powered-soc-slice-1.md`
```

- [ ] **Step 4: Push branch + open PR**

```bash
git add docs/customer/drift-detection-aws.md TEST_PLAN.md HANDOFF.md
git commit -m "docs(soc-s1): customer drift-detection doc + Slice 1 manual gate + HANDOFF block"

git push -u origin docs/ai-powered-soc-spec  # the branch that holds the design spec
# (or whichever branch this slice is built on — likely a new feat/ branch)

gh pr create --title "feat(soc-s1): AI-powered SOC — Slice 1 (AWS Config drift + AI enrichment + /soc + push)" \
  --body "$(cat <<'EOF'
## Summary

- Schema: AI fields + source_event_id + indices + drift target_resource_arn
- event_router: source_event_id dedupe (ON CONFLICT), Config drift severity rule table, before/after state extraction, push rule + per-tenant rate limit, SQS enqueue
- New soc_enrichment Lambda: statistical features → LiteLLM (claude-sonnet-4-6 default, swappable via SOC_ENRICHMENT_LLM_MODEL env) → UPDATE events
- events_list: projects AI fields; new GET /events/{event_id} detail + POST /events/{event_id}/feedback
- aws-onboard.yaml: ConfigRecordingMode parameter (essentials default — ~$30-80/mo vs $200+ for all)
- web: new /soc page (timeline + filter chips + detail pane + feedback)
- docs: customer drift-detection-aws.md; TEST_PLAN Slice 1 gate; HANDOFF block

Spec: `docs/superpowers/specs/2026-05-25-ai-powered-soc-design.md`
Plan: `docs/superpowers/plans/2026-05-25-ai-powered-soc-slice-1.md`

## Test plan

- [ ] All Python pytest suites green (event_router, soc_enrichment, events_list)
- [ ] Web Vitest green (Soc.test.tsx)
- [ ] Manual TEST_PLAN gate "SOC Slice 1" passes end-to-end on test AWS account
- [ ] CloudWatch logs show no enrichment Lambda errors over 24h post-deploy
- [ ] DynamoDB soc_llm_spend_daily shows non-zero spend after the manual gate (cap not reached)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Summary

**What ships (Slice 1):**
- `/soc` web page with live drift feed + AI narrative + anomaly classification + suggested next steps + feedback
- iPhone push within 60s of any AWS drift event matching push rules
- Per-tenant daily LLM spend cap + per-tenant hourly push rate limit
- AWS Config essentials recording profile (lower customer cost)
- Idempotent ingestion via `source_event_id`

**What's deferred (later slices):**
- Slice 1c: threat-intel feed adapters + TI badges in detail pane
- Slice 2: identity drift (AWS IAM via CloudTrail + Entra audit log)
- Slice 3: baseline activation (statistical features wire into AI prompt with 7d+ history)
- Slice 4: Azure (no Sentinel) + GCP

**Test plan:**
- Python pytest (event_router/, soc_enrichment/, events_list/) — all green
- Web Vitest (Soc.test.tsx) — all green
- Manual TEST_PLAN gate on test AWS account
- 24h CloudWatch monitoring post-deploy for enrichment Lambda errors

**Forward compatibility preserved:**
- `mitre_technique` + `incident_id` columns ready for future Kill-Chain / Incident Correlator sub-project
- `drift_events.target_resource_arn` ready for future Entity Graph sub-project
- LiteLLM abstraction ready for cheaper/local model swap or per-tenant residency override
- `ai_features` JSONB captures every structured signal fed to the LLM — auditability hook

---

## Self-Review

**Spec coverage:** Every numbered section of the design spec has tasks:
- §3 slice plan → this entire plan is Slice 1
- §4 architecture → Tasks 1-8 build the pipeline; §4.3 ops guarantees enforced by Tasks 4, 6, 10
- §5 components → Tasks 2 (CDK), 4 (router extension), 8-10 (enrichment), 11-12 (API), 14 (web)
- §6 data model → Task 1 (schema migration)
- §7 data flow → Tasks 3-10 implement t=0 to t=20s lifecycle
- §8 error handling → Task 4 (dedupe), Task 6 (rate limit), Task 8 (DLQ via CDK queue config), Task 10 (cap_reached + unavailable)
- §9 testing → every Task has unit tests; Task 15 has the manual E2E gate
- §10 customer onboarding → Task 13 (CFN refinement) + Task 15 (docs)

**Placeholder scan:** No TBD/TODO. All code blocks complete. Commands have expected output.

**Type consistency:**
- `source_event_id` (TEXT, nullable) used consistently across Tasks 1, 3, 4
- `ai_anomaly_class` values `'expected' | 'unusual' | 'suspicious'` consistent across Tasks 1, 8, 10, 14
- Function name `compute_features` consistent across Tasks 8, 9
- `call_llm` signature `(row, features) -> dict` consistent across Tasks 8, 10
- DynamoDB table name `soc_llm_spend_daily` consistent across Tasks 2, 6, 10
- LiteLLM model env var `SOC_ENRICHMENT_LLM_MODEL` consistent across Tasks 8, 10
- API path `/events/{event_id}` and `/events/{event_id}/feedback` consistent across Tasks 11, 12, 14

**Cleanup noted in code:**
- `spend_cap.py` is copied between event_router and soc_enrichment via build.sh — future cleanup is to lift into a shared `platform/lambda/_shared/` package (Task 10, Step 3 acknowledges this is out of scope for Slice 1).

**No issues found requiring further fixes.**
