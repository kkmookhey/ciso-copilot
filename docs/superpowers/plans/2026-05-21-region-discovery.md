# AWS Region Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every AWS scan discover the account's *active* regions and scan exactly those — replacing the hardcoded `us-east-1` default and the blind 17-region `ai_pass` sweep.

**Architecture:** A new `region_discovery` module runs as the scanner handler's step 0: it lists enabled regions, sweeps each with one `resourcegroupstaggingapi:GetResources` call, and returns the active set (regions with resources, plus `us-east-1`, plus any region whose sweep errored). That set scopes the regional modules, the coverage engine, and — via a `get_enabled_regions()` override on the scanner's AWS client — `ai_pass`.

**Tech Stack:** Python 3.12, pytest, `botocore.stub.Stubber`, boto3, AWS ECS Fargate.

**Spec:** `docs/superpowers/specs/2026-05-21-region-discovery-design.md`.

---

## Conventions

- All Python paths are under `platform/lambda/shasta_runner/` unless stated.
- Run tests with `./.venv/bin/python -m pytest` from `platform/lambda/shasta_runner/` (plain `python`/`python3` lacks pytest).
- `app/tests/conftest.py` puts `app/` on `sys.path` — import new modules by bare name.
- Commit after every task with a Conventional Commit message.
- `app/main.py` imports `shasta.*`, so it CANNOT be imported in the test venv. Changes to it are verified structurally (`ast.parse` + `grep`) and end-to-end in Task 7 — same approach as the Slice-1 plan's Task 7.

## File structure

```
app/region_discovery.py        new — RegionDiscovery, classify_regions, discover_regions
app/main.py                    modified — AssumedRoleAWSClient + handler step-0 wiring
app/run.py                     modified — build_event omits regions when REGIONS env absent
lambda/onboarding_aws_complete/main.py   modified — stop hardcoding regions
```

## Reader-role permissions — already covered, no change needed

Region discovery needs `tag:GetResources` and `ec2:DescribeRegions` on the
customer `CISOCopilotReader` role. That role (`platform/cfn/aws-onboard.yaml`,
`CISOCopilotReaderRole`) attaches the AWS-managed `ReadOnlyAccess` and
`SecurityAudit` policies — `ReadOnlyAccess` grants both actions. No
CloudFormation change is required (spec §10 verified).

---

## Task 1: RegionDiscovery type + classify_regions

**Files:**
- Create: `app/region_discovery.py`
- Test: `app/tests/test_region_discovery.py`

- [ ] **Step 1: Write the failing test**

```python
# app/tests/test_region_discovery.py
"""Region discovery — classify_regions turns per-region probe results
into the active/skipped/errored breakdown the scanner scopes itself to."""
from region_discovery import RegionDiscovery, classify_regions


def test_active_regions_are_those_with_resources_plus_us_east_1():
    enabled = ["us-east-1", "us-west-2", "eu-west-1"]
    probe = {"us-east-1": False, "us-west-2": True, "eu-west-1": False}
    rd = classify_regions(enabled, probe)
    # us-west-2 has resources; us-east-1 is always included (global anchor).
    assert rd.active_regions == ["us-east-1", "us-west-2"]
    assert rd.skipped_empty == ["eu-west-1"]
    assert rd.errored_regions == []
    assert rd.method == "tagging_api"


def test_errored_region_is_treated_as_active_never_skipped():
    enabled = ["us-east-1", "ap-south-1"]
    probe = {"us-east-1": True, "ap-south-1": None}  # None = sweep errored
    rd = classify_regions(enabled, probe)
    assert "ap-south-1" in rd.active_regions
    assert rd.errored_regions == ["ap-south-1"]
    assert rd.skipped_empty == []


def test_us_east_1_active_even_when_empty_and_never_in_skipped():
    rd = classify_regions(["us-east-1"], {"us-east-1": False})
    assert rd.active_regions == ["us-east-1"]
    assert rd.skipped_empty == []


def test_account_empty_everywhere_still_scans_us_east_1():
    enabled = ["us-east-1", "us-west-2"]
    rd = classify_regions(enabled, {"us-east-1": False, "us-west-2": False})
    assert rd.active_regions == ["us-east-1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_region_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'region_discovery'`.

- [ ] **Step 3: Implement the type + classifier**

```python
# app/region_discovery.py
"""AWS region discovery — the scanner's step 0.

Before scanning, determine which regions the account actually uses, so
the scan covers the customer's real footprint: never the hardcoded
us-east-1 default, never a blind sweep of all ~17 enabled regions.

Detection: list enabled regions, then one resourcegroupstaggingapi
GetResources call per region — a region is active if it returns any
resource. See docs/superpowers/specs/2026-05-21-region-discovery-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass

# Always scanned: global services (IAM, CloudFront, Route 53, STS) anchor
# in us-east-1.
_GLOBAL_ANCHOR = "us-east-1"

# Used only when region enumeration itself fails — a documented, non-silent
# fallback (method is reported as 'degraded_default'). The common high-use
# AWS regions; better an over-broad scan than a silent miss.
_DEGRADED_DEFAULT_REGIONS = (
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-west-1", "eu-central-1", "ap-south-1",
    "ap-southeast-1", "ap-southeast-2", "ap-northeast-1",
)


@dataclass(frozen=True)
class RegionDiscovery:
    """Outcome of region discovery for one scan."""
    active_regions:  list[str]   # regions to scan (sorted; includes us-east-1)
    enabled_regions: list[str]   # all opted-in regions enumerated
    skipped_empty:   list[str]   # enabled, swept clean, deliberately skipped
    errored_regions: list[str]   # sweep errored — included in active_regions
    method:          str         # 'tagging_api' | 'degraded_default'


def classify_regions(
    enabled_regions: list[str],
    probe: dict[str, bool | None],
) -> RegionDiscovery:
    """Turn per-region probe results into a RegionDiscovery.

    probe[region] is True (has resources), False (empty), or None (the
    sweep errored). A region is active if it is NOT a positive 'empty'
    result — i.e. True or None both count as active. us-east-1 is always
    active and never appears in skipped_empty.
    """
    active = {_GLOBAL_ANCHOR}
    skipped: list[str] = []
    errored: list[str] = []
    for region, has_resources in probe.items():
        if has_resources is None:
            errored.append(region)
            active.add(region)
        elif has_resources:
            active.add(region)
        elif region != _GLOBAL_ANCHOR:
            skipped.append(region)
    return RegionDiscovery(
        active_regions=sorted(active),
        enabled_regions=sorted(enabled_regions),
        skipped_empty=sorted(skipped),
        errored_regions=sorted(errored),
        method="tagging_api",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_region_discovery.py -v`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner/app/region_discovery.py \
        platform/lambda/shasta_runner/app/tests/test_region_discovery.py
git commit -m "feat: add region discovery classifier"
```

---

## Task 2: discover_regions orchestrator

**Files:**
- Modify: `app/region_discovery.py` — add `discover_regions` + helpers.
- Modify: `app/tests/test_region_discovery.py` — add orchestrator tests.

- [ ] **Step 1: Write the failing test**

Append to `app/tests/test_region_discovery.py`:

```python
import boto3
from botocore.stub import Stubber

from region_discovery import discover_regions


def _ec2_stub(region_names):
    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ec2)
    stub.add_response(
        "describe_regions",
        {"Regions": [{"RegionName": r} for r in region_names]},
    )
    stub.activate()
    return ec2


def _tagging_stub(has_resources: bool):
    tag = boto3.client("resourcegroupstaggingapi", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(tag)
    mappings = [{"ResourceARN": "arn:aws:sqs:...:q"}] if has_resources else []
    stub.add_response("get_resources", {"ResourceTagMappingList": mappings})
    stub.activate()
    return tag


def test_discover_regions_splits_active_and_empty():
    ec2 = _ec2_stub(["us-east-1", "eu-west-1"])
    clients = {"us-east-1": _tagging_stub(True), "eu-west-1": _tagging_stub(False)}
    rd = discover_regions(ec2, lambda r: clients[r])
    assert rd.active_regions == ["us-east-1"]
    assert rd.skipped_empty == ["eu-west-1"]
    assert rd.method == "tagging_api"


def test_discover_regions_treats_sweep_error_as_active():
    ec2 = _ec2_stub(["us-east-1", "ap-south-1"])

    def tagging_for(region):
        if region == "ap-south-1":
            raise RuntimeError("AccessDenied")
        return _tagging_stub(True)

    rd = discover_regions(ec2, tagging_for)
    assert "ap-south-1" in rd.active_regions
    assert rd.errored_regions == ["ap-south-1"]


def test_discover_regions_degrades_when_region_listing_fails():
    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(ec2)
    stub.add_client_error("describe_regions", "UnauthorizedOperation")
    stub.activate()

    rd = discover_regions(ec2, lambda r: None)
    assert rd.method == "degraded_default"
    assert "us-east-1" in rd.active_regions
    assert len(rd.active_regions) > 1  # the documented default set
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_region_discovery.py -v`
Expected: FAIL — `ImportError: cannot import name 'discover_regions'`.

- [ ] **Step 3: Implement the orchestrator**

Add to `app/region_discovery.py` (after `classify_regions`):

```python
def _list_enabled_regions(ec2_client) -> list[str]:
    """Enabled (opted-in or opt-in-not-required) regions for the account."""
    resp = ec2_client.describe_regions(
        Filters=[{"Name": "opt-in-status",
                  "Values": ["opt-in-not-required", "opted-in"]}],
    )
    return [r["RegionName"] for r in resp.get("Regions", [])]


def _region_has_resources(tagging_client, region: str) -> bool:
    """True if the region holds at least one taggable resource."""
    resp = tagging_client.get_resources(ResourcesPerPage=1)
    return bool(resp.get("ResourceTagMappingList"))


def discover_regions(ec2_client, tagging_client_for_region) -> RegionDiscovery:
    """Discover the account's active regions.

    `ec2_client` is a boto3 EC2 client (any region). `tagging_client_for_region`
    is a callable region -> boto3 resourcegroupstaggingapi client bound to
    that region.

    If region enumeration itself fails, returns a RegionDiscovery with
    method='degraded_default' over a documented fallback region set —
    never a silent narrowing.
    """
    try:
        enabled = _list_enabled_regions(ec2_client)
    except Exception as e:
        print(f"region_discovery: describe_regions failed ({e}); "
              f"falling back to degraded default region set")
        return RegionDiscovery(
            active_regions=sorted(_DEGRADED_DEFAULT_REGIONS),
            enabled_regions=[],
            skipped_empty=[],
            errored_regions=[],
            method="degraded_default",
        )

    probe: dict[str, bool | None] = {}
    for region in enabled:
        try:
            probe[region] = _region_has_resources(
                tagging_client_for_region(region), region)
        except Exception as e:
            print(f"region_discovery: sweep failed in {region} ({e}); "
                  f"treating region as active")
            probe[region] = None
    return classify_regions(enabled, probe)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_region_discovery.py -v`
Expected: PASS — 7 tests.

- [ ] **Step 5: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q` — expect all pass.

```bash
git add platform/lambda/shasta_runner/app/region_discovery.py \
        platform/lambda/shasta_runner/app/tests/test_region_discovery.py
git commit -m "feat: add region discovery orchestrator with tagging-API sweep"
```

---

## Task 3: run.py omits regions when REGIONS env is absent

The Fargate entrypoint currently defaults `regions` to `["us-east-1"]` when the `REGIONS` env var is absent. After region discovery exists, an absent `REGIONS` must mean "discover" — so `build_event` must OMIT `regions` entirely rather than default it.

**Files:**
- Modify: `app/run.py` — `build_event`.
- Modify: `app/tests/test_run_entrypoint.py` — update the default-case test.

- [ ] **Step 1: Update the failing test**

In `app/tests/test_run_entrypoint.py`, replace the `test_scan_tier_defaults_to_quick` function with:

```python
def test_no_regions_env_omits_regions_so_scanner_discovers():
    env = {
        "SCAN_ID": "s1", "TENANT_ID": "t1", "CONN_ID": "c1",
        "ROLE_ARN": "r", "EXTERNAL_ID": "x", "ACCOUNT_ID": "111111111111",
    }
    event = build_event(env)
    assert event["scan_tier"] == "quick"
    # No REGIONS env -> 'regions' is absent so main.py runs region discovery.
    assert "regions" not in event
```

Leave `test_build_event_maps_env_to_event` (which passes `REGIONS`) and `test_missing_required_var_raises` unchanged.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_run_entrypoint.py -v`
Expected: FAIL — `test_no_regions_env_omits_regions_so_scanner_discovers` fails: `build_event` still sets `event["regions"] == ["us-east-1"]`.

- [ ] **Step 3: Update build_event**

In `app/run.py`, find the `build_event` function. Its `regions` handling currently reads:

```python
    regions = env.get("REGIONS", "").strip()
    event["regions"] = [r.strip() for r in regions.split(",") if r.strip()] or ["us-east-1"]
    return event
```

Replace those three lines with:

```python
    regions = [r.strip() for r in env.get("REGIONS", "").split(",") if r.strip()]
    if regions:
        # An explicit REGIONS override; otherwise omit 'regions' so the
        # scanner's region-discovery pre-pass picks the scan scope.
        event["regions"] = regions
    return event
```

Also update the `build_event` docstring line that says "regions is comma-split" — change it to: `REGIONS is comma-split into an explicit override, or omitted so the scanner discovers regions.`

- [ ] **Step 4: Run test to verify it passes**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/test_run_entrypoint.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 5: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q` — expect all pass.

```bash
git add platform/lambda/shasta_runner/app/run.py \
        platform/lambda/shasta_runner/app/tests/test_run_entrypoint.py
git commit -m "feat: omit regions from Fargate event when unset so scanner discovers"
```

---

## Task 4: AssumedRoleAWSClient scopes get_enabled_regions

`ai_pass` (Shasta's `discover_aws_ai_services` and the in-repo `discover_bedrock_and_ai_lambdas`) sweeps every region returned by `get_enabled_regions()`. Override that method on `AssumedRoleAWSClient` to return the scan's discovered active regions instead.

**Files:**
- Modify: `app/main.py` — `AssumedRoleAWSClient`.

- [ ] **Step 1: Add `scan_regions` to the constructor**

In `app/main.py`, find `AssumedRoleAWSClient.__init__`. It currently reads:

```python
    def __init__(self, credentials: dict[str, str], region: str, account_id: str):
        super().__init__(region=region)
        self._credentials = credentials
        self._account_info = AWSAccountInfo(
```

Change the signature and add the `_scan_regions` assignment:

```python
    def __init__(self, credentials: dict[str, str], region: str, account_id: str,
                 scan_regions: list[str] | None = None):
        super().__init__(region=region)
        self._credentials = credentials
        self._scan_regions = scan_regions
        self._account_info = AWSAccountInfo(
```

(Leave the rest of `__init__` — the `AWSAccountInfo(...)` block — exactly as it is.)

- [ ] **Step 2: Override get_enabled_regions**

In `app/main.py`, in the `AssumedRoleAWSClient` class, find the existing `client` method override (added by the timeout fix — `def client(self, service_name...`). Immediately after that method, add:

```python
    def get_enabled_regions(self) -> list[str]:
        """Scope AI discovery (and any region-iterating Shasta check) to
        the scan's discovered active regions, not all ~17 enabled regions.
        Falls back to Shasta's all-enabled enumeration if no scan scope
        was supplied."""
        if self._scan_regions:
            return list(self._scan_regions)
        return super().get_enabled_regions()
```

- [ ] **Step 3: Thread scan_regions through for_region**

In `app/main.py`, find the `for_region` method override on `AssumedRoleAWSClient` (added by the timeout fix). It currently reads:

```python
    def for_region(self, region: str) -> "AssumedRoleAWSClient":
        """..."""
        return AssumedRoleAWSClient(self._credentials, region, self._account_info.account_id)
```

Change the return to carry `scan_regions` forward:

```python
        return AssumedRoleAWSClient(self._credentials, region,
                                    self._account_info.account_id,
                                    scan_regions=self._scan_regions)
```

(Keep the method's docstring.)

- [ ] **Step 4: Verify main.py still parses**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -c "import ast; ast.parse(open('app/main.py').read()); print('main.py parses OK')"`
Expected: `main.py parses OK`.

Run: `grep -n "scan_regions\|get_enabled_regions" app/main.py`
Expected: `scan_regions` in the `__init__` signature, the `_scan_regions` assignment, the `get_enabled_regions` override, and the `for_region` passthrough — all present.

- [ ] **Step 5: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q` — expect all pass (no test imports `main`; the suite must simply still be green).

```bash
git add platform/lambda/shasta_runner/app/main.py
git commit -m "feat: scope AssumedRoleAWSClient region enumeration to scan regions"
```

---

## Task 5: Wire region discovery into the scanner handler

The handler runs discovery as step 0 (unless the scan event carries an explicit `regions` override), uses the discovered set everywhere, and records it on the scan row.

**Files:**
- Modify: `app/main.py` — imports, `handler`, a new `_record_scan_scope` helper.

- [ ] **Step 1: Add the discovery import**

In `app/main.py`, in the `# === Entity-emission helpers (this module) ===` import group (where `from coverage.engine import run_coverage`, `from arn_to_entity import parse_arn` etc. live), add:

```python
from region_discovery import discover_regions
```

- [ ] **Step 2: Replace the top-of-handler regions line**

In `app/main.py`'s `handler`, find:

```python
    regions     = event.get("regions") or ["us-east-1"]
    scan_tier   = event.get("scan_tier", "quick")
    print(f"scan start: scan={scan_id} account={account_id} regions={regions} tier={scan_tier}")
```

Replace those three lines with:

```python
    explicit_regions = event.get("regions")
    scan_tier   = event.get("scan_tier", "quick")
    print(f"scan start: scan={scan_id} account={account_id} tier={scan_tier}")
```

(`regions` is no longer set here — it is computed by discovery after the role is assumed, in Step 3.)

- [ ] **Step 3: Add the discovery block after the session is built**

In `app/main.py`'s `handler`, find these two lines (inside the main `try:`):

```python
        credentials = _assume_role(role_arn, external_id)
        boto_session = _make_session(credentials, "us-east-1")
```

Immediately after them, insert:

```python
        # --- Step 0: region discovery. Pick the regions to scan from the
        # account's real footprint. An explicit event 'regions' overrides
        # discovery (operator re-scan of a specific region).
        if explicit_regions:
            regions = list(explicit_regions)
            region_discovery = None
            print(f"region scope: explicit override {regions}")
        else:
            region_discovery = discover_regions(
                boto_session.client("ec2", config=SCAN_BOTO_CONFIG),
                lambda r: _make_session(credentials, r).client(
                    "resourcegroupstaggingapi", config=SCAN_BOTO_CONFIG),
            )
            regions = region_discovery.active_regions
            print(f"region discovery: method={region_discovery.method} "
                  f"active={regions} "
                  f"skipped_empty={len(region_discovery.skipped_empty)} "
                  f"errored={region_discovery.errored_regions}")
        _record_scan_scope(scan_id, regions, region_discovery)
```

From here on `regions` holds the discovered (or overridden) active set, so every existing downstream use of `regions` — the `for region in regions:` loop, `run_coverage(..., regions=regions, ...)`, and the `_update_scan` stats — works unchanged.

- [ ] **Step 4: Pass scan_regions into the AssumedRoleAWSClient constructions**

In `app/main.py`'s `handler` there are three `AssumedRoleAWSClient(...)` constructions. Add `scan_regions=regions` to each:

- In the global-modules loop: `AssumedRoleAWSClient(credentials, "us-east-1", account_id)` → `AssumedRoleAWSClient(credentials, "us-east-1", account_id, scan_regions=regions)`
- In the regional-modules loop: `AssumedRoleAWSClient(credentials, region, account_id)` → `AssumedRoleAWSClient(credentials, region, account_id, scan_regions=regions)`
- For the AI pass: `AssumedRoleAWSClient(credentials, "us-east-1", account_id)` → `AssumedRoleAWSClient(credentials, "us-east-1", account_id, scan_regions=regions)`

Use `grep -n "AssumedRoleAWSClient(credentials" app/main.py` to find all three call sites and confirm each got `scan_regions=regions`.

- [ ] **Step 5: Add the `_record_scan_scope` helper**

In `app/main.py`, in the section of module-level helper functions near `_update_scan` (the `# ==== Legacy scans table updates ====` area), add:

```python
def _record_scan_scope(scan_id: str, regions: list[str], discovery) -> None:
    """Write the region-discovery outcome to scans.scope so the scanned /
    skipped / errored breakdown is auditable per scan. `discovery` is a
    RegionDiscovery, or None when an explicit region override was used."""
    if discovery is None:
        scope = {"regions": regions,
                 "discovery": {"method": "explicit_override"}}
    else:
        scope = {
            "regions":         regions,
            "enabled_regions": discovery.enabled_regions,
            "skipped_empty":   discovery.skipped_empty,
            "discovery": {"method":          discovery.method,
                          "errored_regions": discovery.errored_regions},
        }
    rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=("UPDATE scans SET scope = CAST(:scope AS JSONB) "
             "WHERE scan_id = CAST(:sid AS UUID)"),
        parameters=[
            {"name": "sid",   "value": {"stringValue": scan_id}},
            {"name": "scope", "value": {"stringValue": json.dumps(scope)}},
        ],
    )
```

- [ ] **Step 6: Verify main.py still parses and is wired**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -c "import ast; ast.parse(open('app/main.py').read()); print('main.py parses OK')"`
Expected: `main.py parses OK`.

Run: `grep -n "discover_regions\|_record_scan_scope\|explicit_regions\|scan_regions=regions" app/main.py`
Expected: the `from region_discovery import discover_regions` import; `explicit_regions` (declaration + the `if explicit_regions:` use); the `discover_regions(...)` call; `_record_scan_scope` (definition + call); and `scan_regions=regions` on all three `AssumedRoleAWSClient` constructions.

- [ ] **Step 7: Run the full suite and commit**

Run: `cd platform/lambda/shasta_runner && ./.venv/bin/python -m pytest app/tests/ -q` — expect all pass.

```bash
git add platform/lambda/shasta_runner/app/main.py
git commit -m "feat: run region discovery as the scanner's step 0"
```

---

## Task 6: Onboarding stops hardcoding us-east-1

`onboarding_aws_complete` hardcodes the initial scan to `us-east-1`. With discovery, onboarding must name no region — the scanner discovers.

**Files:**
- Modify: `platform/lambda/onboarding_aws_complete/main.py` — `_enqueue_initial_scan`.

- [ ] **Step 1: Drop the REGIONS container override**

In `platform/lambda/onboarding_aws_complete/main.py`, in `_enqueue_initial_scan`, the `ecs.run_task` call has a `containerOverrides` environment list. Remove this one entry from that list:

```python
                        {"name": "REGIONS",     "value": "us-east-1"},
```

Leave every other environment entry (`SCAN_ID`, `TENANT_ID`, `CONN_ID`, `ROLE_ARN`, `EXTERNAL_ID`, `ACCOUNT_ID`, `SCAN_TIER`) exactly as is. With `REGIONS` absent, `run.py`'s `build_event` omits `regions`, so the scanner runs region discovery.

- [ ] **Step 2: Drop the hardcoded scope on the scans INSERT**

In the same `_enqueue_initial_scan`, the `INSERT INTO scans` statement sets `scope` from a parameter. Find:

```python
            {"name": "scope", "value": {"stringValue": json.dumps({"regions": ["us-east-1"]})}},
```

Replace it with:

```python
            {"name": "scope", "value": {"stringValue": json.dumps({})}},
```

The scanner overwrites `scans.scope` with the real discovery outcome via `_record_scan_scope` (Task 5). An empty object is the correct placeholder until then.

- [ ] **Step 3: Verify it parses**

Run: `./.venv/bin/python -c "import ast; ast.parse(open('platform/lambda/onboarding_aws_complete/main.py').read()); print('OK')"` from the repo root (or use any Python 3 — this file has no special deps).
Expected: `OK`.

Run: `grep -n "REGIONS\|us-east-1" platform/lambda/onboarding_aws_complete/main.py`
Expected: no `REGIONS` override and no `us-east-1` literal remain in `_enqueue_initial_scan`.

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/onboarding_aws_complete/main.py
git commit -m "feat: stop hardcoding us-east-1 in onboarding so scans auto-discover regions"
```

---

## Task 7: Build, deploy, and verify end-to-end

**Files:** none (build + deploy + verification).

This task also closes the Slice-1 loose end — once `ai_pass` is scoped to active regions, scans are fast, so the Quick-vs-Medium tier difference is verified directly here.

- [ ] **Step 1: Rebuild and push the scanner image**

Run: `cd platform/lambda/shasta_runner && ./build.sh`
Expected: ends with `==> done. Image URI: ...:latest`. The Fargate task definition pulls `latest` — no CDK change.

- [ ] **Step 2: Deploy the onboarding Lambda change**

Run: `cd platform && npx cdk deploy CisoCopilotApi --require-approval never`
Expected: `CisoCopilotApi` deploys (it carries the `onboarding_aws_complete` code asset). If the asset bundling does not pick up the change, confirm the stack updated the `OnboardingAwsCompleteFn` resource.

- [ ] **Step 3: Run a MEDIUM-tier scan with NO regions (discovery path)**

Find an active AWS connection. Query for one:

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT conn_id::text, tenant_id::text, account_identifier, credentials_secret_arn FROM cloud_connections WHERE cloud='aws' AND status='active' LIMIT 1"
```

If no active AWS connection exists, STOP and report BLOCKED. From the row, fetch `role_arn` + `external_id`:

```bash
aws secretsmanager get-secret-value --secret-id <credentials_secret_arn> --query SecretString --output text
```

Get the scan network config from the onboarding Lambda's env vars:

```bash
aws lambda list-functions --query 'Functions[?contains(FunctionName,`OnboardingAwsComplete`)].FunctionName' --output text
aws lambda get-function-configuration --function-name <fn> --query 'Environment.Variables.{subnets:SCAN_SUBNET_IDS,sg:SCAN_SECURITY_GROUP_ID}'
```

Generate `<scan-uuid-medium>`. Insert the scan row (`scope` starts empty — the scanner overwrites it via `_record_scan_scope`):

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "INSERT INTO scans (scan_id, tenant_id, conn_id, trigger, status, tier, scope) VALUES (CAST('<scan-uuid-medium>' AS UUID), CAST('<tenant>' AS UUID), CAST('<conn>' AS UUID), 'manual', 'queued', 'medium', CAST('{}' AS JSONB))"
```

Start the Fargate task — **omit the `REGIONS` env entry entirely** so discovery runs:

```bash
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
      {"name":"SCAN_TIER","value":"medium"}]}]}'
```

- [ ] **Step 4: Wait for the medium task**

Poll `aws ecs describe-tasks --cluster ciso-copilot-scan --tasks <task-arn> --query 'tasks[0].lastStatus'` every ~60s until `STOPPED`. With `ai_pass` now scoped to active regions, expect completion in well under the previous times — if it is still RUNNING after 30 minutes, capture logs and report DONE_WITH_CONCERNS.

- [ ] **Step 5: Confirm discovery ran and was recorded**

When STOPPED, confirm container `scanner` exitCode is 0. In the task logs (log group: `aws logs describe-log-groups --query 'logGroups[?contains(logGroupName,\`ScanTaskDef\`)].logGroupName' --output text`) confirm a `region discovery: method=tagging_api active=[...] skipped_empty=N errored=[...]` line and a final `scan complete:` line.

Confirm `scans.scope` was written:

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT scope FROM scans WHERE scan_id=CAST('<scan-uuid-medium>' AS UUID)"
```

Expected: a JSON object with `regions`, `enabled_regions`, `skipped_empty`, and `discovery.method = "tagging_api"`. `regions` should be the account's real footprint (includes `us-east-1`); `enabled_regions` ~17.

- [ ] **Step 6: Run a QUICK-tier scan and confirm the tier difference**

Repeat Steps 3-4 with a fresh `<scan-uuid-quick>`, `tier='quick'` in the INSERT, `SCAN_TIER=quick` in the overrides (still no `REGIONS`). When STOPPED:

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT DISTINCT check_id FROM findings WHERE scan_id=CAST('<scan-uuid-quick>' AS UUID) AND check_id LIKE ANY (ARRAY['sqs-%','secretsmanager-%','ecr-%']) ORDER BY check_id"
```

Expected: only the quick-tier coverage-engine checks (`sqs-encryption-at-rest`, `sqs-queue-not-public`) — not `sqs-dlq-configured`, `secretsmanager-*`, or `ecr-*` (those are `min_tier=medium`). This confirms the Slice-1 tier filter end-to-end. If the account has no SQS resources, confirm the difference from the `coverage:` log lines instead.

- [ ] **Step 7: No commit**

Verification only. If a step reveals a defect, report it — do not attempt code fixes; the controller triages.

---

## Self-review checklist (for the implementer, before declaring this slice done)

- [ ] `./.venv/bin/python -m pytest app/tests/ -q` — all green from `platform/lambda/shasta_runner/`.
- [ ] A real scan with no `REGIONS` env discovered the account's regions, wrote `scans.scope` with the breakdown, and skipped empty regions.
- [ ] The medium scan completed fast (no 17-region `ai_pass` crawl).
- [ ] A quick scan ran strictly fewer coverage-engine check types than the medium scan.
- [ ] No CDK/infra change beyond redeploying the `CisoCopilotApi` code asset.
