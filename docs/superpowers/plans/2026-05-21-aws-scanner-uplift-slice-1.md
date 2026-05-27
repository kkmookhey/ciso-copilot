# AWS Scanner Uplift — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the in-repo posture check engine and prove it end-to-end with three new AWS services (SQS, Secrets Manager, ECR), tier-filtered and wired into the live scanner.

**Architecture:** A small declarative check engine in `app/coverage/`: per-service *collectors* fetch and normalize AWS resources; declarative *Check* objects evaluate one resource and return an Outcome; a *registry* holds all checks and filters by scan tier; an *engine* runs the collectors + checks and emits entities/edges/findings. The engine is wired into `main.py` alongside the existing Shasta + `ai_pass` passes and commits through the existing `unified_writer`.

**Tech Stack:** Python 3.12, pytest, `botocore.stub.Stubber` for collector tests, boto3, AWS ECS Fargate.

**Spec:** `docs/superpowers/specs/2026-05-20-aws-scanner-uplift-design.md` §6 (posture coverage engine), §3 (tier model), §8 (scorecard).

---

## Conventions

- All Python paths are under `platform/lambda/shasta_runner/app/` unless stated.
- Run tests with `./.venv/bin/python -m pytest` from `platform/lambda/shasta_runner/` (plain `python`/`python3` lacks pytest).
- `app/tests/conftest.py` puts `app/` on `sys.path`, so import the new package by bare name: `from coverage.model import Check`.
- The `coverage/` package already exists (Slice 0: `__init__.py`, `benchmarks/`, `shasta_manifest.py`, `scorecard.py`).
- Commit after every task with a Conventional Commit message.
- Emission types (`EntityEmission`, `EdgeEmission`, `FindingEmission`) are defined in `app/detectors/base.py` — at test time they resolve via the sibling `ai_scanner/` dir that `conftest.py` adds to `sys.path`; at runtime `build.sh` copies them into `app/detectors/`.

## Design decisions (read before starting)

- **`Check.evaluate` takes one argument** — `evaluate(resource: Resource) -> Outcome`. The spec §6 illustratively wrote `evaluate(resource, account_ctx)`; no Slice-1 check needs account-level context, so a second parameter would be dead weight on every check. When a check genuinely needs account context (Slice 2+), that is a deliberate signature change then.
- **Three services:** SQS, Secrets Manager, ECR. The spec §6 listed "ECS, SQS, Secrets Manager" as examples; ECR replaces ECS because ECR has clean per-repository posture checks readable from a single `describe_repositories` call, whereas ECS's meaningful checks are task-definition-level (deferred to Slice 2).
- **Entity kind** is derived: `aws_{service}_{resource_type}` (e.g. `aws_sqs_queue`). The `unified_writer._domain_for` maps any `aws_*` kind to domain `cloud` — no writer change needed.
- **Tier semantics:** a Check's `min_tier` is the *lowest* tier at which it runs. A `quick` scan runs only `min_tier=quick` checks; `medium` runs `quick`+`medium`; `deep` runs all.

## File structure

```
app/coverage/
  model.py                  Resource, Outcome, Check dataclasses (Task 1)
  collectors/
    __init__.py
    sqs.py                  collect() -> list[Resource]            (Task 2)
    secretsmanager.py                                              (Task 3)
    ecr.py                                                         (Task 4)
  checks/
    __init__.py
    sqs.py                  CHECKS: list[Check]                    (Task 2)
    secretsmanager.py                                              (Task 3)
    ecr.py                                                         (Task 4)
  registry.py               ALL_CHECKS, COLLECTORS, checks_for_tier (Task 5)
  engine.py                 run_coverage()                          (Task 6)
app/main.py                 wire engine into the handler            (Task 7)
scripts/gen_scorecard.py    include engine checks in coverage map   (Task 8)
```

---

## Task 1: Coverage model types

**Files:**
- Create: `app/coverage/model.py`
- Test: `app/tests/test_coverage_model.py`

- [ ] **Step 1: Write the failing test**

```python
# app/tests/test_coverage_model.py
"""The coverage model types — Resource, Outcome, Check — carry the data
the engine passes between collectors, checks, and emission."""
from coverage.model import Check, Outcome, Resource


def _resource(**over):
    base = dict(service="sqs", resource_type="queue",
                arn="arn:aws:sqs:us-east-1:111:q1", name="q1",
                region="us-east-1", raw={"x": 1})
    base.update(over)
    return Resource(**base)


def test_resource_carries_normalized_fields():
    r = _resource()
    assert r.service == "sqs"
    assert r.arn.endswith("q1")
    assert r.raw == {"x": 1}


def test_outcome_defaults_remediation_empty():
    o = Outcome("pass", {"k": "v"})
    assert o.status == "pass"
    assert o.remediation == ""


def test_check_holds_metadata_and_callable_evaluate():
    chk = Check(
        check_id="x-1", service="sqs", resource_type="queue",
        title="t", severity="medium", domain="encryption",
        min_tier="quick", frameworks={"fsbp": ["SQS.1"]},
        evaluate=lambda r: Outcome("pass", {}),
    )
    out = chk.evaluate(_resource())
    assert out.status == "pass"
    assert chk.frameworks["fsbp"] == ["SQS.1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_coverage_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coverage.model'`.

- [ ] **Step 3: Implement the model**

```python
# app/coverage/model.py
"""Core types for the AWS posture coverage engine.

A collector turns AWS API responses into Resource objects. A Check is a
declarative, deterministic posture rule that evaluates one Resource and
returns an Outcome. The engine runs checks over collected resources and
emits entities/edges/findings. See spec §6.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Resource:
    """One discovered AWS resource, normalized by a collector."""
    service:       str             # boto3 service / client name, e.g. 'sqs'
    resource_type: str             # e.g. 'queue'
    arn:           str             # canonical ARN — the entity natural_key
    name:          str             # human display name
    region:        str
    raw:           dict[str, Any]  # normalized attributes the checks read


@dataclass(frozen=True)
class Outcome:
    """Result of evaluating one Check against one Resource."""
    status:      str               # 'pass' | 'fail' | 'partial'
    evidence:    dict[str, Any]
    remediation: str = ""


@dataclass(frozen=True)
class Check:
    """A declarative, deterministic posture check for one resource type.

    `evaluate` is a pure function: same Resource in, same Outcome out, no
    I/O, no LLM (the determinism invariant, spec §6).
    """
    check_id:      str
    service:       str
    resource_type: str
    title:         str
    severity:      str                     # 'low'|'medium'|'high'|'critical'
    domain:        str                     # finding category, e.g. 'encryption'
    min_tier:      str                     # 'quick'|'medium'|'deep'
    frameworks:    dict[str, list[str]]     # benchmark name -> control ids
    evaluate:      Callable[[Resource], Outcome]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_coverage_model.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner/app/coverage/model.py \
        platform/lambda/shasta_runner/app/tests/test_coverage_model.py
git commit -m "feat: add coverage engine model types"
```

---

## Task 2: SQS — collector + checks

**Files:**
- Create: `app/coverage/collectors/__init__.py`, `app/coverage/collectors/sqs.py`
- Create: `app/coverage/checks/__init__.py`, `app/coverage/checks/sqs.py`
- Test: `app/tests/test_collector_sqs.py`, `app/tests/test_checks_sqs.py`

- [ ] **Step 1: Create the package markers**

Create `app/coverage/collectors/__init__.py` with one line:

```python
"""Per-service AWS resource collectors for the coverage engine."""
```

Create `app/coverage/checks/__init__.py` with one line:

```python
"""Per-service declarative posture checks for the coverage engine."""
```

- [ ] **Step 2: Write the failing collector test**

```python
# app/tests/test_collector_sqs.py
"""The SQS collector lists queues and normalizes each into a Resource
carrying the queue's attributes."""
import boto3
from botocore.stub import Stubber

from coverage.collectors.sqs import collect


def test_collect_normalizes_queue_attributes():
    sqs = boto3.client("sqs", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(sqs)
    stub.add_response(
        "list_queues",
        {"QueueUrls": ["https://sqs.us-east-1.amazonaws.com/111/q1"]},
    )
    stub.add_response(
        "get_queue_attributes",
        {"Attributes": {
            "QueueArn": "arn:aws:sqs:us-east-1:111:q1",
            "SqsManagedSseEnabled": "true",
        }},
        {"QueueUrl": "https://sqs.us-east-1.amazonaws.com/111/q1",
         "AttributeNames": ["All"]},
    )
    stub.activate()

    resources = collect(sqs, account_id="111", region="us-east-1")

    assert len(resources) == 1
    r = resources[0]
    assert r.service == "sqs"
    assert r.resource_type == "queue"
    assert r.arn == "arn:aws:sqs:us-east-1:111:q1"
    assert r.name == "q1"
    assert r.region == "us-east-1"
    assert r.raw["SqsManagedSseEnabled"] == "true"


def test_collect_handles_no_queues():
    sqs = boto3.client("sqs", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(sqs)
    stub.add_response("list_queues", {})
    stub.activate()

    assert collect(sqs, account_id="111", region="us-east-1") == []
```

- [ ] **Step 3: Run collector test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_collector_sqs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coverage.collectors.sqs'`.

- [ ] **Step 4: Implement the SQS collector**

```python
# app/coverage/collectors/sqs.py
"""Collect SQS queues as coverage-engine Resources."""
from __future__ import annotations

from coverage.model import Resource


def collect(client, *, account_id: str, region: str) -> list[Resource]:
    """List every SQS queue in `region` and normalize each to a Resource.

    `client` is a region-bound boto3 SQS client. The queue ARN and all
    attributes come from get_queue_attributes(AttributeNames=['All']).
    """
    resources: list[Resource] = []
    queue_urls = client.list_queues().get("QueueUrls", [])
    for url in queue_urls:
        attrs = client.get_queue_attributes(
            QueueUrl=url, AttributeNames=["All"],
        ).get("Attributes", {})
        arn = attrs.get("QueueArn", url)
        name = url.rsplit("/", 1)[-1]
        resources.append(Resource(
            service="sqs", resource_type="queue",
            arn=arn, name=name, region=region, raw=attrs,
        ))
    return resources
```

- [ ] **Step 5: Run collector test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_collector_sqs.py -v`
Expected: PASS — 2 tests.

- [ ] **Step 6: Write the failing checks test**

```python
# app/tests/test_checks_sqs.py
"""SQS posture checks evaluate a queue Resource into an Outcome."""
import json

from coverage.checks.sqs import CHECKS
from coverage.model import Resource

_BY_ID = {c.check_id: c for c in CHECKS}


def _queue(raw):
    return Resource(service="sqs", resource_type="queue",
                    arn="arn:aws:sqs:us-east-1:111:q1", name="q1",
                    region="us-east-1", raw=raw)


def test_encryption_check_passes_with_sse():
    out = _BY_ID["sqs-encryption-at-rest"].evaluate(
        _queue({"SqsManagedSseEnabled": "true"}))
    assert out.status == "pass"


def test_encryption_check_passes_with_kms_key():
    out = _BY_ID["sqs-encryption-at-rest"].evaluate(
        _queue({"KmsMasterKeyId": "alias/aws/sqs"}))
    assert out.status == "pass"


def test_encryption_check_fails_when_unencrypted():
    out = _BY_ID["sqs-encryption-at-rest"].evaluate(_queue({}))
    assert out.status == "fail"
    assert out.remediation


def test_public_policy_check_fails_on_wildcard_principal():
    policy = json.dumps({"Statement": [
        {"Effect": "Allow", "Principal": "*", "Action": "sqs:SendMessage"}]})
    out = _BY_ID["sqs-queue-not-public"].evaluate(_queue({"Policy": policy}))
    assert out.status == "fail"


def test_public_policy_check_partial_when_wildcard_has_condition():
    policy = json.dumps({"Statement": [
        {"Effect": "Allow", "Principal": {"AWS": "*"},
         "Action": "sqs:SendMessage",
         "Condition": {"StringEquals": {"aws:SourceAccount": "111"}}}]})
    out = _BY_ID["sqs-queue-not-public"].evaluate(_queue({"Policy": policy}))
    assert out.status == "partial"


def test_public_policy_check_passes_without_policy():
    out = _BY_ID["sqs-queue-not-public"].evaluate(_queue({}))
    assert out.status == "pass"


def test_dlq_check_fails_without_redrive_policy():
    out = _BY_ID["sqs-dlq-configured"].evaluate(_queue({}))
    assert out.status == "fail"


def test_dlq_check_passes_with_redrive_policy():
    out = _BY_ID["sqs-dlq-configured"].evaluate(
        _queue({"RedrivePolicy": json.dumps({"deadLetterTargetArn": "arn:..."})}))
    assert out.status == "pass"


def test_every_check_is_well_formed():
    for c in CHECKS:
        assert c.service == "sqs" and c.resource_type == "queue"
        assert c.min_tier in ("quick", "medium", "deep")
        assert c.severity in ("low", "medium", "high", "critical")
```

- [ ] **Step 7: Run checks test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_checks_sqs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coverage.checks.sqs'`.

- [ ] **Step 8: Implement the SQS checks**

```python
# app/coverage/checks/sqs.py
"""Posture checks for SQS queues.

SQS get_queue_attributes returns every attribute value as a string;
the checks read r.raw accordingly.
"""
from __future__ import annotations

import json

from coverage.model import Check, Outcome, Resource


def _encryption_at_rest(r: Resource) -> Outcome:
    sse_managed = str(r.raw.get("SqsManagedSseEnabled", "")).lower() == "true"
    kms_key = r.raw.get("KmsMasterKeyId")
    if sse_managed or kms_key:
        return Outcome("pass", {"sqs_managed_sse": sse_managed,
                                "kms_master_key_id": kms_key})
    return Outcome(
        "fail", {"sqs_managed_sse": False, "kms_master_key_id": None},
        remediation="Enable SSE-SQS or assign an SSE-KMS key to the queue.",
    )


def _statement_is_public(stmt: dict) -> bool:
    if stmt.get("Effect") != "Allow":
        return False
    principal = stmt.get("Principal")
    return principal == "*" or (
        isinstance(principal, dict) and "*" in _as_list(principal.get("AWS")))


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _queue_not_public(r: Resource) -> Outcome:
    raw_policy = r.raw.get("Policy")
    if not raw_policy:
        return Outcome("pass", {"policy": None})
    try:
        policy = json.loads(raw_policy)
    except (ValueError, TypeError):
        return Outcome("partial", {"policy": "unparseable"},
                       remediation="Queue policy could not be parsed; review it manually.")
    public = [s for s in _as_list(policy.get("Statement")) if _statement_is_public(s)]
    if not public:
        return Outcome("pass", {"public_statements": 0})
    # A wildcard principal guarded by a Condition is a softer finding.
    conditioned = all(s.get("Condition") for s in public)
    status = "partial" if conditioned else "fail"
    return Outcome(
        status,
        {"public_statements": len(public), "all_conditioned": conditioned},
        remediation="Restrict the queue policy to specific principals, "
                    "or scope wildcard access with a Condition.",
    )


def _dlq_configured(r: Resource) -> Outcome:
    if r.raw.get("RedrivePolicy"):
        return Outcome("pass", {"redrive_policy": True})
    return Outcome(
        "fail", {"redrive_policy": False},
        remediation="Attach a redrive policy pointing at a dead-letter queue.",
    )


CHECKS = [
    Check(
        check_id="sqs-encryption-at-rest", service="sqs", resource_type="queue",
        title="SQS queue should be encrypted at rest",
        severity="medium", domain="encryption", min_tier="quick",
        frameworks={"fsbp": ["SQS.1"], "nist_800_53": ["SC-28"]},
        evaluate=_encryption_at_rest,
    ),
    Check(
        check_id="sqs-queue-not-public", service="sqs", resource_type="queue",
        title="SQS queue policy should not grant public access",
        severity="high", domain="networking", min_tier="quick",
        frameworks={"nist_800_53": ["AC-3", "AC-6"]},
        evaluate=_queue_not_public,
    ),
    Check(
        check_id="sqs-dlq-configured", service="sqs", resource_type="queue",
        title="SQS queue should have a dead-letter queue configured",
        severity="low", domain="monitoring", min_tier="medium",
        frameworks={},
        evaluate=_dlq_configured,
    ),
]
```

- [ ] **Step 9: Run checks test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_checks_sqs.py -v`
Expected: PASS — 9 tests.

- [ ] **Step 10: Verify framework IDs exist in the vendored catalogs**

The scorecard silently drops control ids absent from a benchmark catalog. Confirm `SQS.1` is in the FSBP catalog and `SC-28`/`AC-3`/`AC-6` are in the NIST catalog:

```bash
cd platform/lambda/shasta_runner
./.venv/bin/python -c "import json; ids={c['id'] for c in json.load(open('app/coverage/benchmarks/fsbp.json'))}; print('SQS.1', 'SQS.1' in ids)"
./.venv/bin/python -c "import json; ids={c['id'] for c in json.load(open('app/coverage/benchmarks/nist_800_53.json'))}; print('SC-28', 'SC-28' in ids, 'AC-3', 'AC-3' in ids, 'AC-6', 'AC-6' in ids)"
```

Expected: all `True`. If `SQS.1` is NOT in `fsbp.json`, find the correct FSBP control id for SQS encryption in `fsbp.json` (grep for `SQS`) and correct the `frameworks` dict in `sqs.py`; if no SQS control exists, drop the `fsbp` key from that check. Same logic for any NIST id. Do not keep an id that is not in the catalog.

- [ ] **Step 11: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q` — expect all pass.

```bash
git add platform/lambda/shasta_runner/app/coverage/collectors/ \
        platform/lambda/shasta_runner/app/coverage/checks/ \
        platform/lambda/shasta_runner/app/tests/test_collector_sqs.py \
        platform/lambda/shasta_runner/app/tests/test_checks_sqs.py
git commit -m "feat: add SQS collector and posture checks"
```

---

## Task 3: Secrets Manager — collector + checks

**Files:**
- Create: `app/coverage/collectors/secretsmanager.py`, `app/coverage/checks/secretsmanager.py`
- Test: `app/tests/test_collector_secretsmanager.py`, `app/tests/test_checks_secretsmanager.py`

- [ ] **Step 1: Write the failing collector test**

```python
# app/tests/test_collector_secretsmanager.py
"""The Secrets Manager collector lists secrets and normalizes each into
a Resource carrying the SecretListEntry fields."""
import boto3
from botocore.stub import Stubber

from coverage.collectors.secretsmanager import collect


def test_collect_normalizes_secrets():
    sm = boto3.client("secretsmanager", region_name="us-east-1",
                      aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(sm)
    stub.add_response(
        "list_secrets",
        {"SecretList": [{
            "ARN": "arn:aws:secretsmanager:us-east-1:111:secret:db-x",
            "Name": "db-x",
            "RotationEnabled": True,
            "KmsKeyId": "arn:aws:kms:us-east-1:111:key/abc",
        }]},
    )
    stub.activate()

    resources = collect(sm, account_id="111", region="us-east-1")

    assert len(resources) == 1
    r = resources[0]
    assert r.service == "secretsmanager"
    assert r.resource_type == "secret"
    assert r.arn == "arn:aws:secretsmanager:us-east-1:111:secret:db-x"
    assert r.name == "db-x"
    assert r.raw["RotationEnabled"] is True


def test_collect_paginates():
    sm = boto3.client("secretsmanager", region_name="us-east-1",
                      aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(sm)
    stub.add_response("list_secrets", {
        "SecretList": [{"ARN": "arn:a:b:c:111:secret:s1", "Name": "s1"}],
        "NextToken": "tok",
    })
    stub.add_response("list_secrets", {
        "SecretList": [{"ARN": "arn:a:b:c:111:secret:s2", "Name": "s2"}],
    }, {"NextToken": "tok"})
    stub.activate()

    resources = collect(sm, account_id="111", region="us-east-1")
    assert sorted(r.name for r in resources) == ["s1", "s2"]
```

- [ ] **Step 2: Run collector test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_collector_secretsmanager.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the Secrets Manager collector**

```python
# app/coverage/collectors/secretsmanager.py
"""Collect Secrets Manager secrets as coverage-engine Resources."""
from __future__ import annotations

from coverage.model import Resource


def collect(client, *, account_id: str, region: str) -> list[Resource]:
    """List every Secrets Manager secret in `region`.

    `client` is a region-bound boto3 secretsmanager client. list_secrets
    returns RotationEnabled and KmsKeyId inline on each SecretListEntry.
    """
    resources: list[Resource] = []
    paginator = client.get_paginator("list_secrets")
    for page in paginator.paginate():
        for entry in page.get("SecretList", []):
            arn = entry.get("ARN", "")
            resources.append(Resource(
                service="secretsmanager", resource_type="secret",
                arn=arn, name=entry.get("Name", arn),
                region=region, raw=entry,
            ))
    return resources
```

- [ ] **Step 4: Run collector test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_collector_secretsmanager.py -v`
Expected: PASS — 2 tests.

- [ ] **Step 5: Write the failing checks test**

```python
# app/tests/test_checks_secretsmanager.py
"""Secrets Manager posture checks."""
from coverage.checks.secretsmanager import CHECKS
from coverage.model import Resource

_BY_ID = {c.check_id: c for c in CHECKS}


def _secret(raw):
    return Resource(service="secretsmanager", resource_type="secret",
                    arn="arn:aws:secretsmanager:us-east-1:111:secret:s",
                    name="s", region="us-east-1", raw=raw)


def test_rotation_passes_when_enabled():
    out = _BY_ID["secretsmanager-rotation-enabled"].evaluate(
        _secret({"RotationEnabled": True}))
    assert out.status == "pass"


def test_rotation_fails_when_disabled():
    out = _BY_ID["secretsmanager-rotation-enabled"].evaluate(
        _secret({"RotationEnabled": False}))
    assert out.status == "fail"


def test_rotation_fails_when_absent():
    out = _BY_ID["secretsmanager-rotation-enabled"].evaluate(_secret({}))
    assert out.status == "fail"


def test_cmk_passes_with_customer_managed_key():
    out = _BY_ID["secretsmanager-cmk-encryption"].evaluate(
        _secret({"KmsKeyId": "arn:aws:kms:us-east-1:111:key/abc"}))
    assert out.status == "pass"


def test_cmk_partial_on_default_aws_managed_key():
    out = _BY_ID["secretsmanager-cmk-encryption"].evaluate(
        _secret({"KmsKeyId": "alias/aws/secretsmanager"}))
    assert out.status == "partial"


def test_cmk_partial_when_key_absent():
    # No KmsKeyId means the secret uses the default aws/secretsmanager key.
    out = _BY_ID["secretsmanager-cmk-encryption"].evaluate(_secret({}))
    assert out.status == "partial"
```

- [ ] **Step 6: Run checks test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_checks_secretsmanager.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 7: Implement the Secrets Manager checks**

```python
# app/coverage/checks/secretsmanager.py
"""Posture checks for Secrets Manager secrets."""
from __future__ import annotations

from coverage.model import Check, Outcome, Resource

# A secret with no KmsKeyId, or one pointing at the account default key,
# is encrypted with the AWS-managed aws/secretsmanager key.
_DEFAULT_KEY_MARKERS = ("alias/aws/secretsmanager",)


def _rotation_enabled(r: Resource) -> Outcome:
    if r.raw.get("RotationEnabled") is True:
        return Outcome("pass", {"rotation_enabled": True})
    return Outcome(
        "fail", {"rotation_enabled": False},
        remediation="Enable automatic rotation on the secret.",
    )


def _cmk_encryption(r: Resource) -> Outcome:
    key = r.raw.get("KmsKeyId")
    if key and not any(m in key for m in _DEFAULT_KEY_MARKERS):
        return Outcome("pass", {"kms_key_id": key})
    return Outcome(
        "partial", {"kms_key_id": key or None,
                    "note": "uses the default aws/secretsmanager key"},
        remediation="Encrypt the secret with a customer-managed KMS key "
                    "for independent key control and audit.",
    )


CHECKS = [
    Check(
        check_id="secretsmanager-rotation-enabled",
        service="secretsmanager", resource_type="secret",
        title="Secrets Manager secret should have automatic rotation enabled",
        severity="medium", domain="iam", min_tier="medium",
        frameworks={"fsbp": ["SecretsManager.1"], "nist_800_53": ["IA-5"]},
        evaluate=_rotation_enabled,
    ),
    Check(
        check_id="secretsmanager-cmk-encryption",
        service="secretsmanager", resource_type="secret",
        title="Secrets Manager secret should use a customer-managed KMS key",
        severity="low", domain="encryption", min_tier="medium",
        frameworks={"nist_800_53": ["SC-28"]},
        evaluate=_cmk_encryption,
    ),
]
```

- [ ] **Step 8: Run checks test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_checks_secretsmanager.py -v`
Expected: PASS — 6 tests.

- [ ] **Step 9: Verify framework IDs exist in the catalogs**

```bash
cd platform/lambda/shasta_runner
./.venv/bin/python -c "import json; ids={c['id'] for c in json.load(open('app/coverage/benchmarks/fsbp.json'))}; print('SecretsManager.1', 'SecretsManager.1' in ids)"
./.venv/bin/python -c "import json; ids={c['id'] for c in json.load(open('app/coverage/benchmarks/nist_800_53.json'))}; print('IA-5', 'IA-5' in ids, 'SC-28', 'SC-28' in ids)"
```

Expected: all `True`. If `SecretsManager.1` is not in `fsbp.json`, grep `fsbp.json` for `SecretsManager` to find the correct rotation control id and correct `secretsmanager.py`; if none fits, drop the `fsbp` key. Same for any NIST id.

- [ ] **Step 10: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q` — expect all pass.

```bash
git add platform/lambda/shasta_runner/app/coverage/collectors/secretsmanager.py \
        platform/lambda/shasta_runner/app/coverage/checks/secretsmanager.py \
        platform/lambda/shasta_runner/app/tests/test_collector_secretsmanager.py \
        platform/lambda/shasta_runner/app/tests/test_checks_secretsmanager.py
git commit -m "feat: add Secrets Manager collector and posture checks"
```

---

## Task 4: ECR — collector + checks

**Files:**
- Create: `app/coverage/collectors/ecr.py`, `app/coverage/checks/ecr.py`
- Test: `app/tests/test_collector_ecr.py`, `app/tests/test_checks_ecr.py`

- [ ] **Step 1: Write the failing collector test**

```python
# app/tests/test_collector_ecr.py
"""The ECR collector lists private repositories and normalizes each into
a Resource carrying the repository description."""
import boto3
from botocore.stub import Stubber

from coverage.collectors.ecr import collect


def test_collect_normalizes_repositories():
    ecr = boto3.client("ecr", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ecr)
    stub.add_response(
        "describe_repositories",
        {"repositories": [{
            "repositoryArn": "arn:aws:ecr:us-east-1:111:repository/app",
            "repositoryName": "app",
            "imageTagMutability": "IMMUTABLE",
            "imageScanningConfiguration": {"scanOnPush": True},
        }]},
    )
    stub.activate()

    resources = collect(ecr, account_id="111", region="us-east-1")

    assert len(resources) == 1
    r = resources[0]
    assert r.service == "ecr"
    assert r.resource_type == "repository"
    assert r.arn == "arn:aws:ecr:us-east-1:111:repository/app"
    assert r.name == "app"
    assert r.raw["imageTagMutability"] == "IMMUTABLE"


def test_collect_paginates():
    ecr = boto3.client("ecr", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ecr)
    stub.add_response("describe_repositories", {
        "repositories": [{"repositoryArn": "arn:...:repository/r1",
                          "repositoryName": "r1"}],
        "nextToken": "tok",
    })
    stub.add_response("describe_repositories", {
        "repositories": [{"repositoryArn": "arn:...:repository/r2",
                          "repositoryName": "r2"}],
    }, {"nextToken": "tok"})
    stub.activate()

    resources = collect(ecr, account_id="111", region="us-east-1")
    assert sorted(r.name for r in resources) == ["r1", "r2"]
```

- [ ] **Step 2: Run collector test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_collector_ecr.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the ECR collector**

```python
# app/coverage/collectors/ecr.py
"""Collect ECR private repositories as coverage-engine Resources."""
from __future__ import annotations

from coverage.model import Resource


def collect(client, *, account_id: str, region: str) -> list[Resource]:
    """List every private ECR repository in `region`.

    `client` is a region-bound boto3 ecr client. describe_repositories
    returns imageTagMutability and imageScanningConfiguration inline.
    """
    resources: list[Resource] = []
    paginator = client.get_paginator("describe_repositories")
    for page in paginator.paginate():
        for repo in page.get("repositories", []):
            arn = repo.get("repositoryArn", "")
            resources.append(Resource(
                service="ecr", resource_type="repository",
                arn=arn, name=repo.get("repositoryName", arn),
                region=region, raw=repo,
            ))
    return resources
```

- [ ] **Step 4: Run collector test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_collector_ecr.py -v`
Expected: PASS — 2 tests.

- [ ] **Step 5: Write the failing checks test**

```python
# app/tests/test_checks_ecr.py
"""ECR posture checks."""
from coverage.checks.ecr import CHECKS
from coverage.model import Resource

_BY_ID = {c.check_id: c for c in CHECKS}


def _repo(raw):
    return Resource(service="ecr", resource_type="repository",
                    arn="arn:aws:ecr:us-east-1:111:repository/app",
                    name="app", region="us-east-1", raw=raw)


def test_scan_on_push_passes_when_enabled():
    out = _BY_ID["ecr-scan-on-push"].evaluate(
        _repo({"imageScanningConfiguration": {"scanOnPush": True}}))
    assert out.status == "pass"


def test_scan_on_push_fails_when_disabled():
    out = _BY_ID["ecr-scan-on-push"].evaluate(
        _repo({"imageScanningConfiguration": {"scanOnPush": False}}))
    assert out.status == "fail"


def test_scan_on_push_fails_when_config_absent():
    out = _BY_ID["ecr-scan-on-push"].evaluate(_repo({}))
    assert out.status == "fail"


def test_tag_immutability_passes_when_immutable():
    out = _BY_ID["ecr-tag-immutability"].evaluate(
        _repo({"imageTagMutability": "IMMUTABLE"}))
    assert out.status == "pass"


def test_tag_immutability_fails_when_mutable():
    out = _BY_ID["ecr-tag-immutability"].evaluate(
        _repo({"imageTagMutability": "MUTABLE"}))
    assert out.status == "fail"
```

- [ ] **Step 6: Run checks test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_checks_ecr.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 7: Implement the ECR checks**

```python
# app/coverage/checks/ecr.py
"""Posture checks for ECR private repositories."""
from __future__ import annotations

from coverage.model import Check, Outcome, Resource


def _scan_on_push(r: Resource) -> Outcome:
    scan_on_push = bool(
        (r.raw.get("imageScanningConfiguration") or {}).get("scanOnPush"))
    if scan_on_push:
        return Outcome("pass", {"scan_on_push": True})
    return Outcome(
        "fail", {"scan_on_push": False},
        remediation="Enable scan-on-push so images are scanned for "
                    "vulnerabilities when pushed.",
    )


def _tag_immutability(r: Resource) -> Outcome:
    immutable = r.raw.get("imageTagMutability") == "IMMUTABLE"
    if immutable:
        return Outcome("pass", {"image_tag_mutability": "IMMUTABLE"})
    return Outcome(
        "fail", {"image_tag_mutability": r.raw.get("imageTagMutability", "MUTABLE")},
        remediation="Set the repository's tag mutability to IMMUTABLE so "
                    "image tags cannot be overwritten.",
    )


CHECKS = [
    Check(
        check_id="ecr-scan-on-push", service="ecr", resource_type="repository",
        title="ECR repository should scan images on push",
        severity="medium", domain="compute", min_tier="medium",
        frameworks={"fsbp": ["ECR.1"], "nist_800_53": ["RA-5"]},
        evaluate=_scan_on_push,
    ),
    Check(
        check_id="ecr-tag-immutability", service="ecr", resource_type="repository",
        title="ECR repository should have tag immutability enabled",
        severity="low", domain="compute", min_tier="medium",
        frameworks={"fsbp": ["ECR.2"], "nist_800_53": ["CM-2"]},
        evaluate=_tag_immutability,
    ),
]
```

- [ ] **Step 8: Run checks test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_checks_ecr.py -v`
Expected: PASS — 5 tests.

- [ ] **Step 9: Verify framework IDs exist in the catalogs**

```bash
cd platform/lambda/shasta_runner
./.venv/bin/python -c "import json; ids={c['id'] for c in json.load(open('app/coverage/benchmarks/fsbp.json'))}; print('ECR.1', 'ECR.1' in ids, 'ECR.2', 'ECR.2' in ids)"
./.venv/bin/python -c "import json; ids={c['id'] for c in json.load(open('app/coverage/benchmarks/nist_800_53.json'))}; print('RA-5', 'RA-5' in ids, 'CM-2', 'CM-2' in ids)"
```

Expected: all `True`. If `ECR.1`/`ECR.2` are not in `fsbp.json`, grep `fsbp.json` for `ECR` — FSBP's scan-on-push and tag-immutability controls — and correct `ecr.py`; if none fits, drop the `fsbp` key for that check. Same for NIST ids.

- [ ] **Step 10: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q` — expect all pass.

```bash
git add platform/lambda/shasta_runner/app/coverage/collectors/ecr.py \
        platform/lambda/shasta_runner/app/coverage/checks/ecr.py \
        platform/lambda/shasta_runner/app/tests/test_collector_ecr.py \
        platform/lambda/shasta_runner/app/tests/test_checks_ecr.py
git commit -m "feat: add ECR collector and posture checks"
```

---

## Task 5: Registry

**Files:**
- Create: `app/coverage/registry.py`
- Test: `app/tests/test_coverage_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# app/tests/test_coverage_registry.py
"""The registry aggregates all checks + collectors and filters by tier."""
from coverage.registry import ALL_CHECKS, COLLECTORS, checks_for_tier


def test_all_checks_are_unique_and_non_empty():
    ids = [c.check_id for c in ALL_CHECKS]
    assert ids, "registry has no checks"
    assert len(ids) == len(set(ids)), "duplicate check_id in registry"


def test_every_check_service_has_a_collector():
    for c in ALL_CHECKS:
        assert c.service in COLLECTORS, f"no collector for service {c.service}"


def test_quick_tier_is_a_subset_of_medium():
    quick = {c.check_id for c in checks_for_tier("quick")}
    medium = {c.check_id for c in checks_for_tier("medium")}
    deep = {c.check_id for c in checks_for_tier("deep")}
    assert quick, "no quick-tier checks"
    assert quick <= medium <= deep
    assert deep == {c.check_id for c in ALL_CHECKS}


def test_quick_tier_excludes_medium_only_checks():
    quick = {c.check_id for c in checks_for_tier("quick")}
    # sqs-dlq-configured is min_tier=medium — must not run at quick.
    assert "sqs-dlq-configured" not in quick
    assert "sqs-encryption-at-rest" in quick
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_coverage_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coverage.registry'`.

- [ ] **Step 3: Implement the registry**

```python
# app/coverage/registry.py
"""The coverage engine registry — the single source of truth for which
posture checks and collectors exist, and which run at a given scan tier.

A check's `min_tier` is the LOWEST tier at which it runs: a 'quick' scan
runs only min_tier=quick checks; 'medium' runs quick+medium; 'deep' runs
all. See spec §3, §6.
"""
from __future__ import annotations

from coverage.checks import ecr as _checks_ecr
from coverage.checks import secretsmanager as _checks_sm
from coverage.checks import sqs as _checks_sqs
from coverage.collectors import ecr as _collect_ecr
from coverage.collectors import secretsmanager as _collect_sm
from coverage.collectors import sqs as _collect_sqs
from coverage.model import Check

ALL_CHECKS: list[Check] = [
    *_checks_sqs.CHECKS,
    *_checks_sm.CHECKS,
    *_checks_ecr.CHECKS,
]

# service name -> collector.collect callable
COLLECTORS = {
    "sqs":            _collect_sqs.collect,
    "secretsmanager": _collect_sm.collect,
    "ecr":            _collect_ecr.collect,
}

_TIER_ORDER = {"quick": 0, "medium": 1, "deep": 2}


def checks_for_tier(tier: str) -> list[Check]:
    """Every check that runs at `tier` — i.e. whose min_tier is at or
    below `tier` in the quick < medium < deep ordering."""
    ceiling = _TIER_ORDER[tier]
    return [c for c in ALL_CHECKS if _TIER_ORDER[c.min_tier] <= ceiling]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_coverage_registry.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner/app/coverage/registry.py \
        platform/lambda/shasta_runner/app/tests/test_coverage_registry.py
git commit -m "feat: add coverage engine registry with tier filtering"
```

---

## Task 6: Engine

**Files:**
- Create: `app/coverage/engine.py`
- Test: `app/tests/test_coverage_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# app/tests/test_coverage_engine.py
"""The engine runs collectors + tier-filtered checks and emits
entities, edges, and findings."""
from coverage import engine
from coverage.model import Resource


class _FakeSession:
    """Stands in for a boto3 Session — .client(name) is never actually
    used because we monkeypatch the collectors."""
    def client(self, name):
        return f"client:{name}"


def _make_session(region):
    return _FakeSession()


def test_engine_emits_entities_edges_findings(monkeypatch):
    # One SQS queue, unencrypted → the quick-tier encryption check fails.
    def fake_sqs_collect(client, *, account_id, region):
        return [Resource(service="sqs", resource_type="queue",
                         arn="arn:aws:sqs:us-east-1:111:q1", name="q1",
                         region=region, raw={})]
    monkeypatch.setitem(engine.COLLECTORS, "sqs", fake_sqs_collect)

    result = engine.run_coverage(
        _make_session, account_id="111", tenant_id="tnt-1",
        regions=["us-east-1"], scan_tier="quick",
    )

    queue_entities = [e for e in result["entities"] if e.kind == "aws_sqs_queue"]
    assert len(queue_entities) == 1
    assert queue_entities[0].natural_key == "arn:aws:sqs:us-east-1:111:q1"
    assert queue_entities[0].domain == "cloud"

    contains = [e for e in result["edges"] if e.kind == "contains"]
    assert any(e.target_natural_key == "arn:aws:sqs:us-east-1:111:q1"
               for e in contains)

    enc = [f for f in result["findings"]
           if f.finding_type == "sqs-encryption-at-rest"]
    assert len(enc) == 1
    assert enc[0].status == "fail"
    assert enc[0].subject_entity_kind == "aws_sqs_queue"
    assert enc[0].region == "us-east-1"
    assert enc[0].domain == "encryption"


def test_engine_respects_scan_tier(monkeypatch):
    def fake_sqs_collect(client, *, account_id, region):
        return [Resource(service="sqs", resource_type="queue",
                         arn="arn:aws:sqs:us-east-1:111:q1", name="q1",
                         region=region, raw={})]
    monkeypatch.setitem(engine.COLLECTORS, "sqs", fake_sqs_collect)
    # Stub the other services' collectors so a medium-tier run does not
    # hit the real collectors with a fake client.
    monkeypatch.setitem(engine.COLLECTORS, "secretsmanager",
                        lambda client, *, account_id, region: [])
    monkeypatch.setitem(engine.COLLECTORS, "ecr",
                        lambda client, *, account_id, region: [])

    quick = engine.run_coverage(
        _make_session, account_id="111", tenant_id="t",
        regions=["us-east-1"], scan_tier="quick")
    medium = engine.run_coverage(
        _make_session, account_id="111", tenant_id="t",
        regions=["us-east-1"], scan_tier="medium")

    quick_checks = {f.finding_type for f in quick["findings"]}
    medium_checks = {f.finding_type for f in medium["findings"]}
    # sqs-dlq-configured is medium-tier — present at medium, absent at quick.
    assert "sqs-dlq-configured" not in quick_checks
    assert "sqs-dlq-configured" in medium_checks


def test_engine_survives_a_failing_collector(monkeypatch):
    def boom(client, *, account_id, region):
        raise RuntimeError("access denied")
    monkeypatch.setitem(engine.COLLECTORS, "sqs", boom)

    # Should not raise — the bad collector is caught and skipped.
    result = engine.run_coverage(
        _make_session, account_id="111", tenant_id="t",
        regions=["us-east-1"], scan_tier="quick")
    assert "findings" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_coverage_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coverage.engine'`.

- [ ] **Step 3: Implement the engine**

```python
# app/coverage/engine.py
"""The coverage engine — runs tier-filtered posture checks over collected
AWS resources and emits entities, edges, and findings.

run_coverage is wrapped by main.py like the other scan passes; one
failing collector (e.g. a permission-denied service) is caught and
skipped so it cannot kill the scan. See spec §6.
"""
from __future__ import annotations

import traceback
from typing import Any, Callable

from detectors.base import EdgeEmission, EntityEmission, FindingEmission

from coverage.registry import COLLECTORS, checks_for_tier

_DETECTOR_ID      = "shasta_runner.coverage"
_DETECTOR_VERSION = "0.1.0"


def run_coverage(
    make_session: Callable[[str], Any], *,
    account_id: str, tenant_id: str,
    regions: list[str], scan_tier: str,
) -> dict[str, list]:
    """Run the coverage engine.

    `make_session(region)` returns a boto3 Session bound to that region.
    Returns {'entities': [...], 'edges': [...], 'findings': [...]}.
    """
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission]   = []
    findings: list[FindingEmission] = []

    checks = checks_for_tier(scan_tier)
    checks_by_service: dict[str, list] = {}
    for c in checks:
        checks_by_service.setdefault(c.service, []).append(c)

    for region in regions:
        session = make_session(region)
        for service, service_checks in checks_by_service.items():
            try:
                client = session.client(service)
                resources = COLLECTORS[service](
                    client, account_id=account_id, region=region)
            except Exception as e:
                print(f"coverage/{service}@{region} collect FAILED: {e}\n"
                      f"{traceback.format_exc()}")
                continue

            for r in resources:
                kind = f"aws_{r.service}_{r.resource_type}"
                entities.append(EntityEmission(
                    tenant_id=tenant_id, kind=kind, natural_key=r.arn,
                    display_name=r.name, domain="cloud",
                    attributes={"service": r.service, "account": account_id,
                                "region": r.region,
                                "resource_type": r.resource_type},
                    evidence_packet=None,
                    detector_id=_DETECTOR_ID, detector_version=_DETECTOR_VERSION,
                ))
                edges.append(EdgeEmission(
                    tenant_id=tenant_id,
                    source_kind="aws_account", source_natural_key=account_id,
                    target_kind=kind, target_natural_key=r.arn,
                    kind="contains", attributes={},
                    evidence_packet={"version": "0.1", "via": "coverage_engine"},
                    detector_id=_DETECTOR_ID, detector_version=_DETECTOR_VERSION,
                ))
                for check in service_checks:
                    if check.resource_type != r.resource_type:
                        continue
                    outcome = check.evaluate(r)
                    findings.append(_to_finding(check, r, outcome, kind, tenant_id))

    return {"entities": entities, "edges": edges, "findings": findings}


def _to_finding(check, r, outcome, kind: str, tenant_id: str) -> FindingEmission:
    return FindingEmission(
        tenant_id=tenant_id,
        finding_type=check.check_id,
        severity=check.severity,
        title=check.title[:500],
        description=(outcome.remediation or "")[:2000],
        subject_entity_kind=kind,
        subject_entity_natural_key=r.arn,
        subject_type=r.resource_type,
        subject_ref=r.arn[:500],
        evidence_packet={
            "version": "0.1",
            "coverage_engine": {
                "check_id":  check.check_id,
                "status":    outcome.status,
                "evidence":  outcome.evidence,
                "remediation": (outcome.remediation or "")[:2000],
                "frameworks": check.frameworks,
            },
        },
        confidence="high",
        frameworks=check.frameworks,
        domain=check.domain,
        status=outcome.status,
        region=r.region,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_coverage_engine.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 5: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q` — expect all pass.

```bash
git add platform/lambda/shasta_runner/app/coverage/engine.py \
        platform/lambda/shasta_runner/app/tests/test_coverage_engine.py
git commit -m "feat: add coverage engine that runs tier-filtered checks"
```

---

## Task 7: Wire the engine into main.py

**Files:**
- Modify: `app/main.py` — add the coverage-engine pass to the handler.

- [ ] **Step 1: Add the engine import**

In `app/main.py`, in the `# === Entity-emission helpers (this module) ===` import group (the block importing `ai_pass`, `arn_to_entity`, `enumerate_*`, `framework_map`), add:

```python
from coverage.engine    import run_coverage
```

- [ ] **Step 2: Add the coverage-engine pass to the handler**

In `app/main.py`'s `handler`, find the AI-pass block. It ends with the `except` clause that sets `module_stats["ai_pass"]`. Immediately AFTER that whole `try/except` block and BEFORE the line `# --- Convert Shasta findings to FindingEmission, derive ARN→entity FKs`, insert:

```python
        # --- Coverage engine: in-repo posture checks, tier-filtered.
        # Wrapped like every other pass so one failure doesn't kill the scan.
        coverage_finding_emissions: list[FindingEmission] = []
        try:
            coverage_result = run_coverage(
                lambda region: _make_session(credentials, region),
                account_id=account_id, tenant_id=tenant_id,
                regions=regions, scan_tier=scan_tier,
            )
            entities.extend(coverage_result["entities"])
            edges.extend(coverage_result["edges"])
            coverage_finding_emissions = coverage_result["findings"]
            module_stats["coverage"] = {
                "entities": len(coverage_result["entities"]),
                "findings": len(coverage_result["findings"]),
                "tier":     scan_tier,
            }
            print(f"coverage: {len(coverage_result['entities'])} entities, "
                  f"{len(coverage_result['findings'])} findings (tier={scan_tier})")
        except Exception as e:
            print(f"coverage FAILED: {e}\n{traceback.format_exc()}")
            module_stats["coverage"] = {"error": str(e)[:200]}
```

- [ ] **Step 3: Include coverage findings in the commit**

In `app/main.py`, find the `commit_scan` call:

```python
        commit_scan(ctx, entities=entities, edges=edges,
                    findings=finding_emissions + ai_finding_emissions)
```

Change it to:

```python
        commit_scan(ctx, entities=entities, edges=edges,
                    findings=finding_emissions + ai_finding_emissions
                             + coverage_finding_emissions)
```

Then find the `total_findings` line:

```python
        total_findings = len(finding_emissions) + len(ai_finding_emissions)
```

Change it to:

```python
        total_findings = (len(finding_emissions) + len(ai_finding_emissions)
                          + len(coverage_finding_emissions))
```

- [ ] **Step 4: Verify main.py still parses and references the engine**

`main.py` cannot be unit-tested by import: it imports `shasta.*` and `detectors.base`, which are only present in the built container, not the test venv (this is why `run.py` from Slice 0 defers `from main import handler` into a function). So verify the edit structurally instead.

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -c "import ast; ast.parse(open('app/main.py').read()); print('main.py parses OK')"`
Expected: `main.py parses OK`.

Run: `grep -n "run_coverage\|coverage_finding_emissions" app/main.py`
Expected: the `from coverage.engine import run_coverage` import, the `run_coverage(...)` call, the two `coverage_finding_emissions` references (declaration + use in `commit_scan`), and the `total_findings` use — all present.

The coverage engine's behavior is fully unit-tested in `test_coverage_engine.py`; the wired-in path is verified end-to-end against a real scan in Task 9.

- [ ] **Step 5: Run the full suite**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q`
Expected: all pass (no new tests in this task — the suite must simply still be green).

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/shasta_runner/app/main.py
git commit -m "feat: run the coverage engine inside the AWS scan"
```

---

## Task 8: Include engine checks in the coverage scorecard

The Slice-0 scorecard counts only Shasta's checks. Now that the engine has checks with `frameworks`, the scorecard's coverage map must include them. The coverage-map construction is moved into `coverage/scorecard.py` so the generator script AND the freshness test share one definition (no duplicated logic).

**Files:**
- Modify: `app/coverage/scorecard.py` — add `build_coverage_map`.
- Modify: `scripts/gen_scorecard.py` — use the shared `build_coverage_map`.
- Modify: `app/tests/test_scorecard_fresh.py` — use the shared `build_coverage_map`.
- Regenerate: `docs/coverage/aws-scorecard.md`, `docs/coverage/aws-scorecard.json`.

- [ ] **Step 1: Add `build_coverage_map` to `coverage/scorecard.py`**

In `app/coverage/scorecard.py`, after the existing imports (`import json`, `from pathlib import Path`, `from typing import Any`), add:

```python
from coverage.registry import ALL_CHECKS
from coverage.shasta_manifest import SHASTA_CHECKS
```

Then add this function to the module (place it after `load_catalogs`):

```python
def build_coverage_map() -> dict[str, dict[str, list[str]]]:
    """The coverage map the scorecard scores.

    Two sources, no overlap in check_ids: Shasta's existing checks (the
    static manifest) and the in-repo coverage engine's checks (the
    registry — each Check declares its own `frameworks`). This is the
    single definition shared by scripts/gen_scorecard.py and the
    scorecard freshness test, so the committed scorecard cannot drift.
    """
    coverage_map: dict[str, dict[str, list[str]]] = dict(SHASTA_CHECKS)
    for check in ALL_CHECKS:
        coverage_map[check.check_id] = dict(check.frameworks)
    return coverage_map
```

- [ ] **Step 2: Point `gen_scorecard.py` at the shared function**

In `platform/lambda/shasta_runner/scripts/gen_scorecard.py`:

- Change the scorecard import line to include `build_coverage_map`:
  ```python
  from coverage.scorecard import (build_coverage_map, compute_scorecard,
                                  load_catalogs, render_markdown)
  ```
- Delete the line `from coverage.shasta_manifest import SHASTA_CHECKS` (no longer used here).
- Delete the local `build_coverage_map` function definition entirely (it now lives in `coverage/scorecard.py`).

`main()` already calls `compute_scorecard(load_catalogs(), build_coverage_map())` — that line is unchanged and now resolves to the imported function.

- [ ] **Step 3: Point the freshness test at the shared function**

In `app/tests/test_scorecard_fresh.py`:

- Change the imports to:
  ```python
  from coverage.scorecard import build_coverage_map, compute_scorecard, load_catalogs
  ```
  (Delete the `from coverage.shasta_manifest import SHASTA_CHECKS` line.)
- Change the body of `test_committed_scorecard_is_current` so `fresh` uses the shared map:
  ```python
  def test_committed_scorecard_is_current():
      fresh = compute_scorecard(load_catalogs(), build_coverage_map())
      committed = json.loads(_SCORECARD_JSON.read_text())
      assert committed == fresh, "stale scorecard — run scripts/gen_scorecard.py"
  ```

This makes the test score the exact same coverage map the generator writes — so it genuinely guards freshness for both Shasta and engine checks.

- [ ] **Step 4: Regenerate the scorecard**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python scripts/gen_scorecard.py`
Expected: prints the four `name: covered/total (pct%)` lines. Coverage is **equal to or higher than** the Slice-0 baseline (cis_aws 74.2%, fsbp 20.5%, pci_dss 9.6%, nist_800_53 10.8%) — the engine checks add a few FSBP/NIST controls (exact lift depends on which framework ids survived the Task 2-4 catalog-verification steps). `docs/coverage/aws-scorecard.{md,json}` are rewritten.

- [ ] **Step 5: Run the freshness test, then the full suite**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_scorecard_fresh.py -v`
Expected: PASS — the committed scorecard matches a fresh `build_coverage_map()` regeneration.

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/shasta_runner/app/coverage/scorecard.py \
        platform/lambda/shasta_runner/scripts/gen_scorecard.py \
        platform/lambda/shasta_runner/app/tests/test_scorecard_fresh.py \
        docs/coverage/aws-scorecard.md docs/coverage/aws-scorecard.json
git commit -m "feat: include coverage engine checks in the scorecard"
```

---

## Task 9: Build, deploy, and verify end-to-end

**Files:** none (build + deploy + verification).

- [ ] **Step 1: Rebuild and push the scanner image**

Run: `cd platform/lambda/shasta_runner && ./build.sh`
Expected: ends with `==> done. Image URI: ...:latest` — the new `coverage/` package and the `main.py` wiring are now in the ECR `latest` image. No CDK change is needed: the Fargate task definition already pulls `latest`.

- [ ] **Step 2: Run a MEDIUM-tier scan**

Use a real active AWS connection. Find one, build the params, and start the Fargate task (this mirrors what Task B5 did in the Slice-0 plan — same cluster `ciso-copilot-scan`, task def `ciso-copilot-aws-scan`, container `scanner`). Query an active connection:

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT conn_id::text, tenant_id::text, account_identifier, credentials_secret_arn FROM cloud_connections WHERE cloud='aws' AND status='active' LIMIT 1"
```

Fetch `role_arn`/`external_id` from the connection's secret (`aws secretsmanager get-secret-value --secret-id <credentials_secret_arn>`). Get subnet + security-group ids from the onboarding Lambda's env vars (`aws lambda get-function-configuration --function-name <onboarding fn> --query 'Environment.Variables'` — keys `SCAN_SUBNET_IDS`, `SCAN_SECURITY_GROUP_ID`).

Insert a `scans` row (generate a UUID for `<scan-uuid-medium>`), then start the task:

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "INSERT INTO scans (scan_id, tenant_id, conn_id, trigger, status, tier, scope) VALUES (CAST('<scan-uuid-medium>' AS UUID), CAST('<tenant>' AS UUID), CAST('<conn>' AS UUID), 'manual', 'queued', 'medium', CAST('{\"regions\":[\"us-east-1\"]}' AS JSONB))"

aws ecs run-task \
  --cluster ciso-copilot-scan --task-definition ciso-copilot-aws-scan \
  --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[<subnet1>,<subnet2>],securityGroups=[<sg-id>],assignPublicIp=DISABLED}' \
  --overrides '{"containerOverrides":[{"name":"scanner","environment":[
      {"name":"SCAN_ID","value":"<scan-uuid-medium>"},
      {"name":"TENANT_ID","value":"<tenant>"},
      {"name":"CONN_ID","value":"<conn>"},
      {"name":"ROLE_ARN","value":"<role_arn>"},
      {"name":"EXTERNAL_ID","value":"<external_id>"},
      {"name":"ACCOUNT_ID","value":"<account_id>"},
      {"name":"REGIONS","value":"us-east-1"},
      {"name":"SCAN_TIER","value":"medium"}]}]}'
```

- [ ] **Step 2b: Wait for the medium task to finish**

Poll `aws ecs describe-tasks --cluster ciso-copilot-scan --tasks <task-arn> --query 'tasks[0].lastStatus'` every ~60s until `STOPPED` (~15+ minutes). Do not tail logs in a blocking follow.

- [ ] **Step 3: Confirm the engine ran and emitted findings**

When STOPPED, check the task log group (stream prefix `aws-scan` — find it with `aws logs describe-log-groups --query 'logGroups[?contains(logGroupName,\`ScanTaskDef\`)].logGroupName'`). Confirm a line like `coverage: N entities, M findings (tier=medium)` appears.

Then query the findings the engine wrote:

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT check_id, count(*) FROM findings WHERE scan_id=CAST('<scan-uuid-medium>' AS UUID) AND check_id LIKE ANY (ARRAY['sqs-%','secretsmanager-%','ecr-%']) GROUP BY check_id ORDER BY check_id"
```

Expected: rows for the engine's check_ids (`sqs-encryption-at-rest`, `sqs-queue-not-public`, `sqs-dlq-configured`, `secretsmanager-rotation-enabled`, `secretsmanager-cmk-encryption`, `ecr-scan-on-push`, `ecr-tag-immutability`) — those that have matching resources in the account. If the account has no SQS/Secrets Manager/ECR resources at all, the engine produces zero findings — note that and confirm via the log line `coverage: 0 entities, 0 findings` instead; the engine still ran correctly.

- [ ] **Step 4: Run a QUICK-tier scan and confirm the tier difference**

Repeat Step 2 with a fresh `<scan-uuid-quick>` and `SCAN_TIER=quick` (and `tier='quick'` in the INSERT). Wait for STOPPED. Then:

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT DISTINCT check_id FROM findings WHERE scan_id=CAST('<scan-uuid-quick>' AS UUID) AND check_id LIKE ANY (ARRAY['sqs-%','secretsmanager-%','ecr-%']) ORDER BY check_id"
```

Expected (when the account has SQS queues): only the quick-tier engine checks appear — `sqs-encryption-at-rest` and `sqs-queue-not-public` — and NOT `sqs-dlq-configured`, `secretsmanager-*`, or `ecr-*` (those are `min_tier=medium`). This is the visible quick-vs-medium tier difference. If the account has no SQS resources, confirm the tier difference instead from the log lines: the quick run logs fewer coverage findings than the medium run.

- [ ] **Step 5: No commit**

Verification only. If a step reveals a defect, fix it in the relevant earlier task and re-verify.

---

## Self-review checklist (for the implementer, before declaring Slice 1 done)

- [ ] `./.venv/bin/python -m pytest app/tests/ -q` — all green from `platform/lambda/shasta_runner/`.
- [ ] The coverage engine ran inside a real Fargate scan (`coverage: … findings (tier=…)` in the logs).
- [ ] Engine findings (`sqs-*`/`secretsmanager-*`/`ecr-*`) are in the `findings` table for the medium scan (or a documented zero, if the account has none of those resources).
- [ ] A quick scan ran strictly fewer engine check types than the medium scan — the tier filter visibly works.
- [ ] `docs/coverage/aws-scorecard.md` regenerated; coverage % did not drop; freshness test passes.
- [ ] No CDK/infra change was needed or made — Slice 1 is code-only on top of the Slice-0 Fargate path.
```
