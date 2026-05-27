# AI Visibility v2 — Slice 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an **AI sign-in pass** to the existing `shasta_runner_entra` that detects which users in a customer's Entra (Azure AD) tenant signed into known AI SaaS apps (ChatGPT, Claude, Cursor, Copilot, Perplexity, Gemini, etc.) — populating the `/ai` view's per-person table and the Entra source tile.

**Architecture:** Piggyback on the existing `cloud_type='entra'` connection (no new connector type, no new admin-consent, no new secret). New file `platform/lambda/shasta_runner_entra/app/ai_signin_pass.py` reads Microsoft Graph `auditLogs/signIns`, matches each event against a curated `ai_saas_catalog.json`, and emits findings tagged with `evidence_packet.is_ai='true'` + `evidence_packet.entra_upn=<user>` so the existing `/ai/summary` Lambda's predicate + per-person query pick them up automatically.

**Tech Stack:** Python 3.12 (container Lambda), Microsoft Graph SDK (already in scanner image via Shasta's `azure.client`), Aurora Postgres via `rds-data`, pytest.

**Spec:** `docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md` (§9 amended 2026-05-23 with the piggyback decision D-1).

**Out of scope for S2 (deferred):**
- Per-tenant sanctioned-app overrides (the `ai_signin_unsanctioned_app` finding kind in the original spec).
- Entity emission for `ai_user_signin` — the entra runner doesn't yet use `unified_writer`; entity rows can land in a future refactor slice. S2 emits findings only; the AI-touching predicate uses the `evidence_packet.is_ai='true'` escape hatch.
- Framework tagging (NIST AI RMF, ISO 42001, SOC 2 AI on signin check_ids) — S3 work.
- Web UI changes — `/ai` page works as-is once findings land.

**Branch:** `feat/ai-visibility-v2-slice-2`.

---

## File Structure

### Created
- `platform/lambda/shasta_runner_entra/app/ai_signin_pass.py` — pure helpers (catalog match, signin→finding mapping) + `run_ai_signin_pass(client, *, tenant_id, conn_id, scan_id, entra_tenant_id, last_scan_at)` orchestrator with lazy Graph import.
- `platform/lambda/shasta_runner_entra/app/ai_saas_catalog.json` — curated ~30-entry list of AI SaaS apps with appDisplayName aliases, appId aliases, default_severity, and tier_inference rules.
- `platform/lambda/shasta_runner_entra/app/tests/__init__.py` (if missing).
- `platform/lambda/shasta_runner_entra/app/tests/test_ai_signin_pass.py` — unit tests.
- `platform/lambda/shasta_runner_entra/app/tests/conftest.py` (only if needed for path setup).

### Modified
- `platform/lambda/shasta_runner_entra/app/main.py` — call `run_ai_signin_pass` after the existing Shasta entra checks; merge its findings into the batch.
- `HANDOFF.md` — prepend S2 ship block on completion.

### Not modified (intentional, save scope creep)
- `platform/lib/api-stack.ts` — no API changes (the existing `/ai/summary` already serves the data).
- `web/src/**` — no web changes.
- `platform/lambda/ai_summary/**` — no read-side changes.
- Shasta — read-only reference per CLAUDE.md.

---

## Task 1: Pre-flight — verify `AuditLog.Read.All` scope

**Goal:** confirm the existing Microsoft AAD app registration has `AuditLog.Read.All` consented at admin-consent time. The entire piggyback plan assumes this. If missing, the plan branches.

**Files touched:** none (read-only investigation). May commit a small note to the plan or HANDOFF if scope is missing.

- [ ] **Step 1: Find the deployed app's manifest**

The existing Entra onboarding lives at `platform/lambda/onboarding_entra_initiate/main.py`. The `ENTRA_APP_ID` env var holds the Microsoft AAD app's client ID. The admin-consent URL is constructed at lines 75-82 and does NOT explicitly specify scopes (Microsoft uses the app registration's declared `requiredResourceAccess` list).

Find the app registration manifest:

```bash
# Get the app ID from the deployed Lambda environment.
aws lambda get-function-configuration \
  --function-name $(aws lambda list-functions --query "Functions[?contains(FunctionName, 'OnboardingEntraInitiate')].FunctionName | [0]" --output text) \
  --query 'Environment.Variables.ENTRA_APP_ID' --output text
```

Take the returned client ID (call it `<APP_ID>`).

- [ ] **Step 2: Inspect the app's required permissions**

The implementer needs Microsoft Graph access to read the app's manifest. Two paths:

**Path A — via Microsoft Graph API** (requires admin in our own Microsoft tenant; the agent likely cannot do this directly):
```
GET https://graph.microsoft.com/v1.0/applications?$filter=appId eq '<APP_ID>'
```
Inspect `requiredResourceAccess[]` for the Microsoft Graph entry (`resourceAppId == '00000003-0000-0000-c000-000000000000'`) and confirm the resource-access list contains the `AuditLog.Read.All` GUID `b0afded3-3588-46d8-8b3d-9842eff778da`.

**Path B — via Azure portal** (KK does it, agent reports):
The agent flags this step to KK with the explicit ask: "open `https://entra.microsoft.com/` → App registrations → find the app with client ID `<APP_ID>` → API permissions → confirm `AuditLog.Read.All` is in the list, status `Granted for <our org>`. Report Yes/No."

Either path resolves the question. Default this step's mode to **Path B** with KK action, since the agent cannot reliably authenticate to our own Entra tenant.

- [ ] **Step 3: Outcome handling**

- **If scope present** → proceed to Task 2.
- **If scope absent** → escalate to KK with two options:
  1. **Add the scope to the app registration.** KK opens the portal, adds the API permission, clicks "Grant admin consent". Existing customer connections continue to work unchanged (their original consent grant included scope subset; adding scopes to the app registration extends what's available, but tenants must re-consent for the new scope to take effect on THEIR tenant — surface this as a one-time banner on `/connect` for tenants where the consent grant precedes the scope addition).
  2. **Fall back to a separate connector.** Implement the spec's original `cloud_type='entra_signin'` path; bigger lift, deferred.

Stop and pick a branch with KK input. Do not proceed silently.

- [ ] **Step 4: Document the outcome**

Add a one-line note to the plan: "Task 1 outcome: scope present (or: added 2026-XX-XX)". This becomes part of the S2 ship block in HANDOFF.

No commit in this task — it's pure investigation. The outcome flows into Task 5 (where we wire the pass into `main.py` confident the scope is available).

---

## Task 2: Ship the AI-SaaS catalog

**Files:**
- Create: `platform/lambda/shasta_runner_entra/app/ai_saas_catalog.json`

**Goal:** a curated JSON file with ~30 well-known AI SaaS apps, their identifying signals (appDisplayName, appId), and per-app risk policy (default severity, tier inference).

**Schema** — top-level object keyed by canonical name; each entry:

```json
{
  "<canonical_name>": {
    "match": {
      "app_display_names": ["<exact name>", "<alias>", ...],
      "app_ids":           ["<aad-app-id-uuid>", ...]
    },
    "default_severity": "low" | "medium" | "high",
    "tier_inference": {
      "<keyword_in_display_name>": "<tier>"
    }
  }
}
```

`tier_inference` is OPTIONAL. When present, the matcher checks if any of its keys appears (case-insensitive substring) in `appDisplayName`; if a key matches, the resolved tier is the value (e.g. `"teams" -> "corp"`, `"enterprise" -> "corp"`). If `tier_inference` is absent OR no key matches, the tier is `"unknown"`. The tier maps to the finding kind:

| Resolved tier | Finding kind | Severity source |
|---|---|---|
| `personal` | `ai_signin_personal_tier` | `default_severity` (typically `high`) |
| `corp` | `ai_signin_corp_tier` | `default_severity` overridden to `low` |
| `unknown` | `ai_signin_unknown_tier` | `default_severity` |

- [ ] **Step 1: Write the file**

```json
{
  "OpenAI": {
    "match": {
      "app_display_names": ["OpenAI", "ChatGPT", "OpenAI ChatGPT", "ChatGPT Enterprise"],
      "app_ids": []
    },
    "default_severity": "high",
    "tier_inference": {
      "enterprise": "corp",
      "teams":      "corp",
      "team":       "corp",
      "edu":        "corp"
    }
  },
  "Anthropic": {
    "match": {
      "app_display_names": ["Anthropic", "Claude", "Anthropic Claude", "Claude.ai"],
      "app_ids": []
    },
    "default_severity": "high",
    "tier_inference": {
      "team":       "corp",
      "enterprise": "corp"
    }
  },
  "GitHub Copilot": {
    "match": {
      "app_display_names": ["GitHub Copilot", "Copilot for Business"],
      "app_ids": []
    },
    "default_severity": "low",
    "tier_inference": null
  },
  "Microsoft Copilot": {
    "match": {
      "app_display_names": ["Microsoft Copilot", "Microsoft 365 Copilot", "Copilot Studio"],
      "app_ids": []
    },
    "default_severity": "low",
    "tier_inference": null
  },
  "Cursor": {
    "match": {
      "app_display_names": ["Cursor", "Cursor IDE", "Cursor AI"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": {
      "business":   "corp",
      "enterprise": "corp"
    }
  },
  "Google Gemini": {
    "match": {
      "app_display_names": ["Google Gemini", "Gemini", "Bard", "Gemini Advanced", "Gemini for Workspace"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": {
      "workspace":  "corp",
      "enterprise": "corp"
    }
  },
  "Perplexity": {
    "match": {
      "app_display_names": ["Perplexity", "Perplexity AI", "Perplexity Pro"],
      "app_ids": []
    },
    "default_severity": "high",
    "tier_inference": {
      "enterprise": "corp"
    }
  },
  "Mistral": {
    "match": {
      "app_display_names": ["Mistral", "Mistral AI", "Le Chat"],
      "app_ids": []
    },
    "default_severity": "high",
    "tier_inference": {
      "enterprise": "corp"
    }
  },
  "Cohere": {
    "match": {
      "app_display_names": ["Cohere"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": null
  },
  "HuggingFace": {
    "match": {
      "app_display_names": ["Hugging Face", "HuggingFace"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": {
      "enterprise": "corp"
    }
  },
  "Replicate": {
    "match": {
      "app_display_names": ["Replicate"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": null
  },
  "Stability AI": {
    "match": {
      "app_display_names": ["Stability AI", "DreamStudio"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": null
  },
  "Midjourney": {
    "match": {
      "app_display_names": ["Midjourney"],
      "app_ids": []
    },
    "default_severity": "high",
    "tier_inference": null
  },
  "Runway": {
    "match": {
      "app_display_names": ["Runway", "Runway ML"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": null
  },
  "ElevenLabs": {
    "match": {
      "app_display_names": ["ElevenLabs"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": {
      "enterprise": "corp"
    }
  },
  "Notion AI": {
    "match": {
      "app_display_names": ["Notion AI", "Notion"],
      "app_ids": []
    },
    "default_severity": "low",
    "tier_inference": null
  },
  "Jasper": {
    "match": {
      "app_display_names": ["Jasper", "Jasper AI"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": null
  },
  "Writer": {
    "match": {
      "app_display_names": ["Writer", "Writer.com"],
      "app_ids": []
    },
    "default_severity": "low",
    "tier_inference": null
  },
  "Otter.ai": {
    "match": {
      "app_display_names": ["Otter.ai", "Otter"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": {
      "business":   "corp",
      "enterprise": "corp"
    }
  },
  "Fireflies": {
    "match": {
      "app_display_names": ["Fireflies", "Fireflies.ai"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": null
  },
  "DeepL": {
    "match": {
      "app_display_names": ["DeepL", "DeepL Pro"],
      "app_ids": []
    },
    "default_severity": "low",
    "tier_inference": null
  },
  "Synthesia": {
    "match": {
      "app_display_names": ["Synthesia"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": null
  },
  "Tabnine": {
    "match": {
      "app_display_names": ["Tabnine"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": {
      "enterprise": "corp"
    }
  },
  "Codeium": {
    "match": {
      "app_display_names": ["Codeium", "Windsurf"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": {
      "teams":      "corp",
      "enterprise": "corp"
    }
  },
  "Sourcegraph Cody": {
    "match": {
      "app_display_names": ["Sourcegraph", "Cody"],
      "app_ids": []
    },
    "default_severity": "low",
    "tier_inference": null
  },
  "DeepSeek": {
    "match": {
      "app_display_names": ["DeepSeek"],
      "app_ids": []
    },
    "default_severity": "high",
    "tier_inference": null
  },
  "Grok / xAI": {
    "match": {
      "app_display_names": ["xAI", "Grok"],
      "app_ids": []
    },
    "default_severity": "high",
    "tier_inference": null
  },
  "Together.ai": {
    "match": {
      "app_display_names": ["Together", "Together AI", "Together.ai"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": null
  },
  "Groq": {
    "match": {
      "app_display_names": ["Groq"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": null
  },
  "AI21": {
    "match": {
      "app_display_names": ["AI21", "AI21 Labs", "Jurassic"],
      "app_ids": []
    },
    "default_severity": "medium",
    "tier_inference": null
  }
}
```

The `app_ids` arrays are left empty intentionally — Microsoft assigns AAD app IDs per multi-tenant SaaS registration, and finding the canonical ID for each app requires real sign-in data to confirm. The matcher uses `app_display_names` as the primary signal; `app_ids` is the upgrade path: once a known appId is observed for a SaaS (from production sign-in events), KK adds it to the catalog and a redeploy ships the more precise match.

- [ ] **Step 2: Verify the JSON parses cleanly**

```bash
python -c "import json; json.load(open('platform/lambda/shasta_runner_entra/app/ai_saas_catalog.json'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/shasta_runner_entra/app/ai_saas_catalog.json
git commit -m "feat: add curated AI-SaaS catalog for Entra sign-in detection"
```

---

## Task 3: Implement `ai_signin_pass.py` (TDD)

**Files:**
- Create: `platform/lambda/shasta_runner_entra/app/ai_signin_pass.py`
- Create: `platform/lambda/shasta_runner_entra/app/tests/__init__.py` (empty, only if missing)
- Create: `platform/lambda/shasta_runner_entra/app/tests/test_ai_signin_pass.py`

The module exposes three functions:
- `load_catalog(path: str) -> dict` — reads + parses the catalog JSON.
- `match_app(event: dict, catalog: dict) -> tuple[str|None, str|None, str|None]` — given one sign-in event, returns `(canonical_name, tier, severity)` or `(None, None, None)` if no match.
- `run_ai_signin_pass(graph_client, *, tenant_id, conn_id, scan_id, entra_tenant_id, last_scan_at=None) -> list[dict]` — pages through Graph, matches each event, returns a list of finding-param dicts ready for `_insert_findings` to write. Graph SDK imported lazily.

**Finding params shape** (compatible with `main.py`'s `_FINDING_INSERT_SQL`):

```python
{
    "name": "fid",           "value": {"stringValue": str(uuid.uuid4())},
    "name": "tid",           "value": {"stringValue": tenant_id},
    "name": "cid",           "value": {"stringValue": conn_id},
    "name": "sid",           "value": {"stringValue": scan_id},
    "name": "check_id",      "value": {"stringValue": "ai_signin_personal_tier" | "ai_signin_corp_tier" | "ai_signin_unknown_tier"},
    "name": "title",         "value": {"stringValue": "<User> signed into <App>"},
    "name": "description",   "value": {"stringValue": "<UPN> authenticated to <App> at <createdDateTime>. Tier: <tier>. ..."},
    "name": "severity",      "value": {"stringValue": "low|medium|high"},
    "name": "status",        "value": {"stringValue": "fail"},  # personal_tier + unknown_tier
                                                                # OR "pass" for corp_tier
    "name": "resource_arn",  "value": {"stringValue": ""},
    "name": "resource_type", "value": {"stringValue": "ai_signin"},
    "name": "region",        "value": {"stringValue": entra_tenant_id},
    "name": "domain",        "value": {"stringValue": "identity"},
    "name": "frameworks",    "value": {"stringValue": "{}"},  # framework tags are S3 work
    "name": "remediation",   "value": {"stringValue": "<per-tier guidance>"},
}
```

⚠ The existing `main.py:_FINDING_INSERT_SQL` does NOT have a column for `evidence_packet`. **Verify** before relying on it — see Task 5 Step 1. The plan assumes evidence_packet WILL be added to the INSERT by Task 5; if not, the per-person view won't populate. **This is the load-bearing schema gap.**

- [ ] **Step 1: Write the first failing test — catalog matching**

```python
# platform/lambda/shasta_runner_entra/app/tests/test_ai_signin_pass.py
"""Unit tests for ai_signin_pass.

Pure helpers (load_catalog, match_app, signin_to_params) are tested
against fixture dicts. run_ai_signin_pass is not unit-tested here —
it's exercised via deployed smoke (Task 7).
"""
from __future__ import annotations

import json
import os
import tempfile

from ai_signin_pass import load_catalog, match_app, signin_to_params


_FIXTURE_CATALOG = {
    "OpenAI": {
        "match": {
            "app_display_names": ["OpenAI", "ChatGPT"],
            "app_ids": ["00000000-aaaa-bbbb-cccc-000000000001"]
        },
        "default_severity": "high",
        "tier_inference": {"enterprise": "corp", "teams": "corp"}
    },
    "GitHub Copilot": {
        "match": {"app_display_names": ["GitHub Copilot"], "app_ids": []},
        "default_severity": "low",
        "tier_inference": None
    }
}


def test_load_catalog_parses_json(tmp_path):
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(_FIXTURE_CATALOG))
    assert load_catalog(str(p)) == _FIXTURE_CATALOG


def test_match_app_by_display_name_personal_tier():
    event = {"appDisplayName": "ChatGPT", "appId": "x"}
    name, tier, sev = match_app(event, _FIXTURE_CATALOG)
    assert name == "OpenAI"
    assert tier == "unknown"          # "ChatGPT" has no tier keyword in it
    assert sev == "high"


def test_match_app_by_display_name_enterprise_inference():
    event = {"appDisplayName": "ChatGPT Enterprise", "appId": "x"}
    name, tier, sev = match_app(event, _FIXTURE_CATALOG)
    assert name == "OpenAI"
    assert tier == "corp"
    # Catalog default_severity stays; the orchestrator decides whether
    # to override severity for corp tier — see signin_to_params.


def test_match_app_by_app_id_when_display_name_missing():
    event = {"appDisplayName": "", "appId": "00000000-aaaa-bbbb-cccc-000000000001"}
    name, tier, sev = match_app(event, _FIXTURE_CATALOG)
    assert name == "OpenAI"


def test_match_app_returns_none_for_non_ai_app():
    event = {"appDisplayName": "Microsoft Teams", "appId": "y"}
    name, tier, sev = match_app(event, _FIXTURE_CATALOG)
    assert name is None
    assert tier is None
    assert sev is None


def test_match_app_handles_missing_tier_inference():
    event = {"appDisplayName": "GitHub Copilot", "appId": "z"}
    name, tier, sev = match_app(event, _FIXTURE_CATALOG)
    assert name == "GitHub Copilot"
    assert tier == "unknown"          # no tier_inference rules → unknown
    assert sev == "low"


def test_signin_to_params_personal_tier_emits_fail_high():
    event = {
        "appDisplayName": "ChatGPT",
        "appId": "00000000-aaaa-bbbb-cccc-000000000001",
        "userPrincipalName": "alice@acme.com",
        "createdDateTime": "2026-05-23T10:00:00Z",
        "id": "signin-evt-1",
    }
    params = signin_to_params(
        event, name="OpenAI", tier="unknown", catalog_severity="high",
        tenant_id="TEN", conn_id="CONN", scan_id="SCAN",
        entra_tenant_id="ETEN",
    )
    by_name = {p["name"]: p["value"]["stringValue"] for p in params}
    assert by_name["check_id"] == "ai_signin_unknown_tier"
    assert by_name["severity"] == "high"
    assert by_name["status"] == "fail"
    assert by_name["domain"] == "identity"
    assert by_name["resource_type"] == "ai_signin"
    assert by_name["region"] == "ETEN"


def test_signin_to_params_corp_tier_emits_pass_low():
    event = {
        "appDisplayName": "ChatGPT Enterprise",
        "appId": "x",
        "userPrincipalName": "bob@acme.com",
        "createdDateTime": "2026-05-23T10:00:00Z",
        "id": "signin-evt-2",
    }
    params = signin_to_params(
        event, name="OpenAI", tier="corp", catalog_severity="high",
        tenant_id="TEN", conn_id="CONN", scan_id="SCAN",
        entra_tenant_id="ETEN",
    )
    by_name = {p["name"]: p["value"]["stringValue"] for p in params}
    assert by_name["check_id"] == "ai_signin_corp_tier"
    assert by_name["severity"] == "low"     # corp tier downgrades severity
    assert by_name["status"] == "pass"      # corp tier is OK posture-wise


def test_signin_to_params_includes_entra_upn_in_evidence():
    """evidence_packet must carry entra_upn for the /ai per-person view to populate."""
    event = {
        "appDisplayName": "ChatGPT", "appId": "x",
        "userPrincipalName": "carol@acme.com",
        "createdDateTime": "2026-05-23T10:00:00Z", "id": "evt",
    }
    params = signin_to_params(
        event, name="OpenAI", tier="unknown", catalog_severity="high",
        tenant_id="TEN", conn_id="CONN", scan_id="SCAN", entra_tenant_id="ETEN",
    )
    by_name = {p["name"]: p["value"]["stringValue"] for p in params}
    ev = json.loads(by_name["evidence_packet"])
    assert ev["entra_upn"] == "carol@acme.com"
    assert ev["is_ai"] == "true"
    assert ev["app"] == "OpenAI"
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_entra/app && \
  /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner/.venv/bin/python -m pytest tests/test_ai_signin_pass.py -v
```

Expected: `ModuleNotFoundError: No module named 'ai_signin_pass'`.

- [ ] **Step 3: Implement `ai_signin_pass.py`**

```python
# platform/lambda/shasta_runner_entra/app/ai_signin_pass.py
"""AI sign-in pass for the Entra runner — Slice 2 of AI Visibility v2.

Reads Microsoft Graph audit-log sign-in events, matches each event
against a curated AI-SaaS catalog, and emits finding-shaped param dicts
ready for the existing _insert_findings batch path in main.py.

Pure helpers (load_catalog, match_app, signin_to_params) are unit-tested
against fixture dicts. run_ai_signin_pass is the orchestrator; it imports
the Graph SDK lazily so this module stays importable in test environments
without the SDK installed.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_DEFAULT_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "ai_saas_catalog.json")

_CHECK_BY_TIER = {
    "personal": "ai_signin_personal_tier",
    "corp":     "ai_signin_corp_tier",
    "unknown":  "ai_signin_unknown_tier",
}

# Status policy: corp tier passes (sanctioned), others fail (actionable).
_STATUS_BY_TIER = {
    "personal": "fail",
    "corp":     "pass",
    "unknown":  "fail",
}


def load_catalog(path: str = _DEFAULT_CATALOG_PATH) -> dict:
    """Read + parse the AI-SaaS catalog JSON."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def match_app(event: dict, catalog: dict) -> tuple[str | None, str | None, str | None]:
    """Match one sign-in event against the catalog.

    Returns (canonical_name, tier, default_severity) on hit, or
    (None, None, None) on miss. Tier is one of 'personal', 'corp',
    'unknown'.
    """
    app_display = (event.get("appDisplayName") or "").strip()
    app_id      = (event.get("appId") or "").strip()
    app_display_lc = app_display.lower()

    for canonical, entry in catalog.items():
        match = entry.get("match", {})
        names = [n.lower() for n in match.get("app_display_names", [])]
        ids   = match.get("app_ids", [])
        hit = False
        if app_id and app_id in ids:
            hit = True
        elif app_display_lc and app_display_lc in names:
            hit = True
        if not hit:
            continue

        # Determine tier.
        tier = "unknown"
        rules = entry.get("tier_inference") or {}
        for keyword, mapped_tier in rules.items():
            if keyword.lower() in app_display_lc:
                tier = mapped_tier
                break

        return canonical, tier, entry.get("default_severity", "medium")

    return None, None, None


def signin_to_params(
    event: dict, *,
    name: str, tier: str, catalog_severity: str,
    tenant_id: str, conn_id: str, scan_id: str, entra_tenant_id: str,
) -> list[dict]:
    """Build the param-list for one finding INSERT, ready for
    _insert_findings in main.py.

    Corp tier downgrades severity to 'low' and status to 'pass' (the
    app is sanctioned). Personal + unknown emit 'fail' at the catalog
    severity. evidence_packet carries `entra_upn` + `is_ai='true'` so
    the /ai per-person query + is_ai_touching predicate pick the
    finding up.
    """
    check_id = _CHECK_BY_TIER[tier]
    status   = _STATUS_BY_TIER[tier]
    severity = "low" if tier == "corp" else catalog_severity

    upn = event.get("userPrincipalName", "") or ""
    created = event.get("createdDateTime", "")

    title = f"{upn or 'unknown user'} signed into {name}"[:500]
    description = (
        f"User {upn or '(unknown)'} authenticated to {name} "
        f"at {created or 'unknown time'}. Tier: {tier}."
    )[:2000]
    remediation = (
        "Review whether the user has access to a corporate-tier instance "
        "of this AI tool with proper data-handling controls."
        if tier == "personal" else
        "Confirm via your AI usage policy whether this sign-in is sanctioned."
    )[:2000]

    evidence_packet = {
        "entra_upn":         upn,
        "is_ai":             "true",
        "app":               name,
        "tier":              tier,
        "signin_id":         event.get("id", ""),
        "created_at":        created,
        "app_display_name":  event.get("appDisplayName", ""),
        "app_id":            event.get("appId", ""),
    }

    return [
        {"name": "fid",             "value": {"stringValue": str(uuid.uuid4())}},
        {"name": "tid",             "value": {"stringValue": tenant_id}},
        {"name": "cid",             "value": {"stringValue": conn_id}},
        {"name": "sid",             "value": {"stringValue": scan_id}},
        {"name": "check_id",        "value": {"stringValue": check_id}},
        {"name": "title",           "value": {"stringValue": title}},
        {"name": "description",     "value": {"stringValue": description}},
        {"name": "severity",        "value": {"stringValue": severity}},
        {"name": "status",          "value": {"stringValue": status}},
        {"name": "resource_arn",    "value": {"stringValue": ""}},
        {"name": "resource_type",   "value": {"stringValue": "ai_signin"}},
        {"name": "region",          "value": {"stringValue": entra_tenant_id[:50]}},
        {"name": "domain",          "value": {"stringValue": "identity"}},
        {"name": "frameworks",      "value": {"stringValue": "{}"}},
        {"name": "remediation",     "value": {"stringValue": remediation}},
        {"name": "evidence_packet", "value": {"stringValue": json.dumps(evidence_packet)}},
    ]


def run_ai_signin_pass(
    graph_client: Any, *,
    tenant_id: str, conn_id: str, scan_id: str, entra_tenant_id: str,
    last_scan_at: str | None = None,
    catalog_path: str | None = None,
) -> list[list[dict]]:
    """Page through Graph audit logs, match against catalog, return a
    list of param-lists ready for _insert_findings.

    Graph SDK is imported lazily so this module stays importable in test
    environments without the SDK installed.
    """
    from azure.identity import DefaultAzureCredential        # type: ignore
    from msgraph import GraphServiceClient                   # type: ignore

    catalog = load_catalog(catalog_path or _DEFAULT_CATALOG_PATH)

    # Reuse the same DefaultAzureCredential the existing Shasta scan
    # already authenticated. graph_client is a GraphServiceClient OR a
    # placeholder; if None, we construct one fresh.
    if graph_client is None:
        credential = DefaultAzureCredential()
        graph_client = GraphServiceClient(credentials=credential, scopes=["https://graph.microsoft.com/.default"])

    out: list[list[dict]] = []
    try:
        events = _fetch_signins(graph_client, last_scan_at=last_scan_at)
    except Exception as e:
        logger.warning("ai_signin_pass: Graph fetch failed: %s", e)
        return out

    for event in events:
        name, tier, sev = match_app(event, catalog)
        if name is None:
            continue
        params = signin_to_params(
            event, name=name, tier=tier, catalog_severity=sev,
            tenant_id=tenant_id, conn_id=conn_id, scan_id=scan_id,
            entra_tenant_id=entra_tenant_id,
        )
        out.append(params)

    return out


def _fetch_signins(graph_client: Any, *, last_scan_at: str | None) -> Iterable[dict]:
    """Page through `/auditLogs/signIns`, yielding plain dict events.

    Incremental by `createdDateTime ge last_scan_at` when provided.
    Drops gracefully on auth/scope errors — caller logs.
    """
    from kiota_abstractions.base_request_configuration import RequestConfiguration  # type: ignore
    from msgraph.generated.audit_logs.sign_ins.sign_ins_request_builder import SignInsRequestBuilder  # type: ignore

    query_params = SignInsRequestBuilder.SignInsRequestBuilderGetQueryParameters(
        top=1000,
    )
    if last_scan_at:
        query_params.filter = f"createdDateTime ge {last_scan_at}"
    cfg = RequestConfiguration(query_parameters=query_params)

    # The SDK's pagination iterator is async; for v1 we collect a single
    # page synchronously via the underlying request. Production-quality
    # paging across many pages is a follow-on.
    page = graph_client.audit_logs.sign_ins.get(request_configuration=cfg)
    page = _maybe_await(page)
    if page is None or not getattr(page, "value", None):
        return []
    return [_event_to_dict(e) for e in page.value]


def _maybe_await(coro: Any) -> Any:
    """The Graph SDK returns coroutines from sync-looking calls.
    Run-to-completion inside a fresh event loop if needed."""
    import asyncio
    import inspect
    if inspect.iscoroutine(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return coro


def _event_to_dict(event: Any) -> dict:
    """Normalize a Graph SignIn object to a plain dict for matching."""
    return {
        "id":                 getattr(event, "id", None),
        "appDisplayName":     getattr(event, "app_display_name", None),
        "appId":              getattr(event, "app_id", None),
        "userPrincipalName":  getattr(event, "user_principal_name", None),
        "createdDateTime":    (getattr(event, "created_date_time", None) or ""),
    }
```

- [ ] **Step 4: Run all unit tests, confirm PASS**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_entra/app && \
  /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner/.venv/bin/python -m pytest tests/test_ai_signin_pass.py -v
```

Expected: 9/9 PASS (the 9 tests written in Step 1).

If `tests/__init__.py` doesn't exist, create an empty one:
```bash
touch /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_entra/app/tests/__init__.py
```

- [ ] **Step 5: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/shasta_runner_entra/app/ai_signin_pass.py \
        platform/lambda/shasta_runner_entra/app/tests/test_ai_signin_pass.py \
        platform/lambda/shasta_runner_entra/app/tests/__init__.py
git commit -m "feat(entra): add ai_signin_pass module + curated catalog matcher"
```

---

## Task 4: Extend `_FINDING_INSERT_SQL` to include `evidence_packet`

**Files:**
- Modify: `platform/lambda/shasta_runner_entra/app/main.py`

**Why:** the current INSERT (line 88-99) does NOT include `evidence_packet`. Without it, the per-person view query (`evidence_packet ->> 'entra_upn'`) finds nothing and the AI-touching predicate's `evidence_packet ->> 'is_ai'` escape hatch doesn't trigger. Even though Shasta's standard findings don't currently use evidence_packet, S2 needs it. We extend the INSERT to accept it as an optional column; existing entra findings just pass `{}`.

- [ ] **Step 1: Verify the live `findings` table accepts evidence_packet (it does — confirmed during S1)**

Already verified — `findings.evidence_packet` is a JSONB column. No DB migration needed.

- [ ] **Step 2: Patch the INSERT SQL**

In `platform/lambda/shasta_runner_entra/app/main.py` around lines 88-99, modify `_FINDING_INSERT_SQL`:

```python
_FINDING_INSERT_SQL = """
INSERT INTO findings (
    finding_id, tenant_id, conn_id, scan_id, check_id, title, description,
    severity, status, resource_arn, resource_type, region, domain,
    frameworks, remediation, evidence_packet, first_seen, last_seen
) VALUES (
    CAST(:fid AS UUID), CAST(:tid AS UUID), CAST(:cid AS UUID), CAST(:sid AS UUID),
    :check_id, :title, :description, :severity, :status, :resource_arn,
    :resource_type, :region, :domain,
    CAST(:frameworks AS JSONB), :remediation, CAST(:evidence_packet AS JSONB),
    now(), now()
)
"""
```

- [ ] **Step 3: Patch `_finding_to_params` to emit `evidence_packet`**

In the same file, modify `_finding_to_params` (around lines 119-147) to include `evidence_packet` in its returned list, defaulting to `'{}'`:

```python
def _finding_to_params(f, scan_id, tenant_id, conn_id, entra_tenant_id):
    frameworks = {
        "soc2":      f.soc2_controls,
        "cis_aws":   f.cis_aws_controls,
        "cis_azure": f.cis_azure_controls,
        "cis_gcp":   f.cis_gcp_controls,
        "mcsb":      f.mcsb_controls,
        "iso27001":  f.iso27001_controls,
        "hipaa":     f.hipaa_controls,
    }
    frameworks = {k: v for k, v in frameworks.items() if v}
    frameworks = merge_framework_map(f.check_id, frameworks)
    return [
        {"name": "fid",              "value": {"stringValue": str(uuid.uuid4())}},
        {"name": "tid",              "value": {"stringValue": tenant_id}},
        {"name": "cid",              "value": {"stringValue": conn_id}},
        {"name": "sid",              "value": {"stringValue": scan_id}},
        {"name": "check_id",         "value": {"stringValue": f.check_id}},
        {"name": "title",            "value": {"stringValue": f.title[:500]}},
        {"name": "description",      "value": {"stringValue": (f.description or "")[:2000]}},
        {"name": "severity",         "value": {"stringValue": f.severity.value.lower()}},
        {"name": "status",           "value": {"stringValue": f.status.value.lower()}},
        {"name": "resource_arn",     "value": {"stringValue": (f.resource_id or "")[:500]}},
        {"name": "resource_type",    "value": {"stringValue": f.resource_type[:200]}},
        {"name": "region",           "value": {"stringValue": (f.region or entra_tenant_id)[:50]}},
        {"name": "domain",           "value": {"stringValue": f.domain.value.lower()}},
        {"name": "frameworks",       "value": {"stringValue": json.dumps(frameworks)}},
        {"name": "remediation",      "value": {"stringValue": (f.remediation or "")[:2000]}},
        {"name": "evidence_packet",  "value": {"stringValue": "{}"}},
    ]
```

- [ ] **Step 4: Add a no-op smoke test asserting both flows still produce a valid param-list**

Optional — if there's an existing test for `_finding_to_params`, update it to also assert the `evidence_packet` key is present. If no such test exists, skip — the change is structural and the deployed-scan smoke (Task 7) verifies end-to-end.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner_entra/app/main.py
git commit -m "feat(entra): findings INSERT now carries evidence_packet"
```

---

## Task 5: Wire `ai_signin_pass` into `main.py`'s handler

**Files:**
- Modify: `platform/lambda/shasta_runner_entra/app/main.py`

- [ ] **Step 1: Add the import**

At the top of the file (with other imports around lines 18-26):

```python
from ai_signin_pass import run_ai_signin_pass
```

- [ ] **Step 2: Wire the call after the Shasta entra checks**

In `handler`, after the existing block that runs Shasta checks + before `_insert_findings`, around lines 62-68, add the AI sign-in pass:

```python
        # ... existing Shasta entra checks ...
        try:
            findings = shasta_entra.run_all_azure_entra_checks(client)
        except Exception as e:
            print(f"entra checks FAILED: {e}\n{traceback.format_exc()}")
            findings = []

        written = _insert_findings(findings, scan_id, tenant_id, conn_id, entra_tenant_id)

        # NEW: AI sign-in pass.
        # Builds finding-param dicts directly (not Shasta Finding objects)
        # and writes them via the same batch path.
        try:
            ai_signin_params = run_ai_signin_pass(
                graph_client=None,             # constructed fresh inside the pass
                tenant_id=tenant_id,
                conn_id=conn_id,
                scan_id=scan_id,
                entra_tenant_id=entra_tenant_id,
            )
        except Exception as e:
            print(f"ai_signin_pass FAILED: {e}\n{traceback.format_exc()}")
            ai_signin_params = []

        if ai_signin_params:
            written += _insert_finding_param_lists(ai_signin_params)

        _update_scan(scan_id, status="completed", stats={
            "findings":        written,
            "entra_tenant_id": entra_tenant_id,
            "module":          "entra",
        })
        # ... rest unchanged
```

- [ ] **Step 3: Add `_insert_finding_param_lists` helper**

Mirrors `_insert_findings` but takes pre-built param lists (from the AI sign-in pass) instead of Shasta Finding objects.

Add near `_insert_findings`:

```python
def _insert_finding_param_lists(param_lists: list[list[dict]]) -> int:
    """Insert findings whose params are already built (AI sign-in pass).

    Mirrors _insert_findings' batching pattern but skips the
    Finding-object-to-params conversion since the caller did it.
    """
    if not param_lists:
        return 0
    written = 0
    for i in range(0, len(param_lists), _BATCH_SIZE):
        batch = param_lists[i : i + _BATCH_SIZE]
        rds_data.batch_execute_statement(
            resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
            sql=_FINDING_INSERT_SQL, parameterSets=batch,
        )
        written += len(batch)
    return written
```

- [ ] **Step 4: Run existing tests, confirm no regressions**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_entra/app && \
  /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner/.venv/bin/python -m pytest tests/ -v
```

Expected: all existing tests + the 9 from Task 3 pass.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/shasta_runner_entra/app/main.py
git commit -m "feat(entra): run ai_signin_pass after Shasta checks; insert via shared batch path"
```

---

## Task 6: Rebuild + push the Entra scanner image, deploy

**Files:** none — build + deploy operations only.

- [ ] **Step 1: Rebuild the image**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda/shasta_runner_entra
./build.sh
```

Expected: ECR push completes; image digest printed.

- [ ] **Step 2: Confirm Lambda picks up the new image**

`shasta_runner_entra` is a container Lambda. Confirm the Lambda's image-URI is `:latest`-tagged (the runner uses `:latest` like the Azure runner). If yes, the next invocation pulls the new image; if no, a `CisoCopilotScan` deploy is needed.

```bash
aws lambda get-function-configuration \
  --function-name ciso-copilot-shasta-runner-entra \
  --query 'PackageType' --output text
```

Should print `Image`. Then check the image URI:

```bash
aws lambda get-function-configuration \
  --function-name ciso-copilot-shasta-runner-entra \
  --query 'Code.ImageUri' --output text
```

Expected output includes `:latest` — if it does, no CDK deploy needed (the next invocation pulls fresh).

If the image URI pins a digest, run a Lambda update:

```bash
aws lambda update-function-code \
  --function-name ciso-copilot-shasta-runner-entra \
  --image-uri $AWS_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/shasta-runner-entra:latest
```

- [ ] **Step 3: No CDK deploy required** unless the image-URI was digest-pinned (Step 2). Document the path taken.

---

## Task 7: Smoke-verify on a deployed Entra-connected tenant

**Files:** none — verification step. Stops for KK to run an actual Entra rescan if needed.

- [ ] **Step 1: Identify a tenant with an Entra connection**

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT conn_id::text, tenant_id::text, display_name, status FROM cloud_connections WHERE cloud_type='entra' AND status='active'"
```

If zero rows: ask KK to onboard an Entra tenant via `/connect` before proceeding.

- [ ] **Step 2: Trigger a rescan**

KK clicks "Scan" on the Entra row at `/scan`, or the agent invokes the rescan API. Wait until `scans` table shows the new scan with `status='completed'`.

- [ ] **Step 3: Confirm AI sign-in findings landed**

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN \
  --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot \
  --sql "SELECT check_id, status, severity, count(*) FROM findings WHERE check_id LIKE 'ai_signin_%' GROUP BY 1, 2, 3 ORDER BY 1, 2"
```

Expected: at least one row, depending on what AI apps the tenant's users have signed into.

If zero rows AND the tenant has known AI users: investigate via `tail` logs:
```bash
aws logs tail "/aws/lambda/ciso-copilot-shasta-runner-entra" --since 10m
```

Likely causes:
- `AuditLog.Read.All` scope not actually granted (Task 1 missed it).
- Catalog doesn't match the actual `appDisplayName` values Microsoft returns. Compare against:
  ```sql
  -- (Not possible against our DB — Graph events aren't stored. Inspect the live Graph API directly.)
  ```
- Graph SDK auth flow failed silently. Tail logs.

- [ ] **Step 4: Confirm `/ai` view picks them up**

KK refreshes `https://$SHASTA_DOMAIN/ai` in an incognito window. Expected changes from S1's baseline:
- **By-source** row: Entra tile no longer 0; shows total AI-touching count (includes the new sign-in findings + any prior entra findings tagged via the predicate).
- **Top AI users**: table populates with users who appeared in the sign-in events, ranked by Fail + Partial counts.
- **Score**: Fail count goes up by the number of `ai_signin_personal_tier` + `ai_signin_unknown_tier` findings.

- [ ] **Step 5: Document the verification outcome**

Note the outcome in the Task 8 HANDOFF block.

---

## Task 8: HANDOFF.md ship block + push + open PR

**Files:**
- Modify: `HANDOFF.md` — prepend S2 ship block.

- [ ] **Step 1: Prepend the S2 ship block**

```markdown
## 🚀 AI Visibility v2 — Slice 2 shipped (2026-MM-DD)

Sub-project **AI Visibility v2**, Slice 2 (S2). Spec
`docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md` (§9
amended 2026-05-23 with decision D-1); plan
`docs/superpowers/plans/2026-05-23-ai-visibility-v2-slice-2.md`. Built
on branch **`feat/ai-visibility-v2-slice-2`** (XXX commits ahead of
`main`).

**S2 — AI sign-in pass inside the existing Entra runner — DONE.**

- **Piggyback architecture** (decision D-1): no new connector type, no
  new admin-consent flow, no new secret. AI sign-in scanning lands as
  an additional pass inside `shasta_runner_entra` alongside Shasta's
  existing compliance checks.
- **Pre-flight (Task 1) outcome:** `AuditLog.Read.All` scope present
  on the deployed Microsoft AAD app (or: added 2026-MM-DD with
  one-time re-consent banner for existing tenants).
- **`ai_signin_pass.py`** (~XXX lines + 9 unit tests): pages through
  Microsoft Graph `auditLogs/signIns`, matches each event against a
  curated `ai_saas_catalog.json` (30 well-known AI SaaS apps), emits
  one finding per matched sign-in carrying `evidence_packet.is_ai=true`
  and `evidence_packet.entra_upn=<user>`. Three finding kinds:
  `ai_signin_personal_tier` (fail/high), `ai_signin_unknown_tier`
  (fail/catalog-severity), `ai_signin_corp_tier` (pass/low).
- **`_FINDING_INSERT_SQL` extended** to carry `evidence_packet` so
  the per-person view's `entra_upn` lookup populates. Existing Shasta
  findings emit `evidence_packet = '{}'` for now (no behavior change).
- **No web changes.** The existing `/ai/summary` Lambda's
  `is_ai_touching` predicate already covered `evidence_packet ->>
  'is_ai' = 'true'` (added during S1 review). Per-person query already
  reads `evidence_packet ->> 'entra_upn'`. /ai page populates
  automatically.
- **No CDK deploy needed.** `ciso-copilot-shasta-runner-entra` Lambda
  uses `:latest` tag; pushed image is picked up on next invocation.

**Live-verification (Task 7) outcome:** [PASTE]

**Deferred from S2 (per plan + spec):**
- Per-tenant sanctioned-app overrides + `ai_signin_unsanctioned_app`
  finding kind.
- Entity emission for `ai_user_signin` (the entra runner doesn't yet
  use `unified_writer`; refactor is a follow-on).
- Framework tagging — NIST AI RMF / ISO 42001 / SOC 2 AI on
  `ai_signin_*` check IDs lands in S3.

**▶ NEXT** — Slice 3 (compliance mapping sweep + EU AI Act registry).
```

- [ ] **Step 2: Commit + push + open PR**

```bash
git add HANDOFF.md
git commit -m "$(cat <<'EOF'
docs(handoff): AI Visibility v2 Slice 2 shipped

Piggyback AI sign-in pass added to shasta_runner_entra. New catalog
+ matcher emit per-tier findings carrying evidence_packet.is_ai +
entra_upn so the existing /ai view populates without read-side
changes. Verification checklist in HANDOFF — KK-gated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"

git push -u origin feat/ai-visibility-v2-slice-2

gh pr create --title "feat: AI Visibility v2 Slice 2 — AI sign-in pass in Entra runner" --body "$(cat <<'EOF'
## Summary
- New `ai_signin_pass.py` in `shasta_runner_entra` reads Microsoft Graph `auditLogs/signIns`, matches against a curated 30-entry AI-SaaS catalog, emits findings carrying `evidence_packet.is_ai=true` + `evidence_packet.entra_upn=<user>`.
- Piggybacks on the existing `cloud_type='entra'` connection — no new connector, no new admin-consent, no new secret.
- `_FINDING_INSERT_SQL` extended with `evidence_packet`; existing Shasta findings carry `{}`.

## Test plan
- [ ] `pytest tests/test_ai_signin_pass.py` green (9 tests)
- [ ] Full entra runner test suite green
- [ ] Image rebuilt + pushed
- [ ] Lambda picks up new image (`:latest` tag — no CDK deploy needed)
- [ ] **KK-gated**: rescan an Entra-connected tenant; confirm `ai_signin_*` findings land; refresh `/ai` and confirm Entra source tile + Top AI Users populate

## Refs
- Spec: `docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md` (§9 amended 2026-05-23, decision D-1)
- Plan: `docs/superpowers/plans/2026-05-23-ai-visibility-v2-slice-2.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review

**Spec coverage** (against `docs/superpowers/specs/2026-05-22-ai-visibility-v2-design.md` §9 amended):

| Spec §9 element | Task |
|---|---|
| 9.1 — no separate connection flow; piggyback decision | Task 1 (pre-flight scope verification) |
| 9.1 — pre-flight scope check | Task 1 |
| 9.2 — `ai_signin_pass.py` lives in shasta_runner_entra/app/ | Task 3 |
| 9.2 — Graph paging incremental by createdDateTime | Task 3 (`_fetch_signins`) |
| 9.2 — match appDisplayName + appId | Task 3 (`match_app`) |
| 9.2 — emit findings with evidence_packet.entra_upn + is_ai=true | Tasks 3 + 4 |
| 9.3 — curated catalog JSON | Task 2 |
| 9.4 — three finding kinds (personal/corp/unknown), deferred unsanctioned | Task 3 (`signin_to_params`, `_CHECK_BY_TIER`) |
| 9.5 — failure modes (scope missing, 403, rate limit, free-tier retention, appId churn) | Tasks 1 + 3 |
| §10 — framework mapping deferred to S3 | Out of scope (called out) |

**Placeholder scan:** `XXX` / `[PASTE]` appear only inside the HANDOFF template at Task 8 Step 1 — intentional placeholders that get filled in at ship time (line count, verification outcome, date). No code-step placeholders.

**Type consistency:**
- Catalog entry shape (`match.app_display_names`, `match.app_ids`, `default_severity`, `tier_inference`) is consistent across Task 2 (JSON) + Task 3 (`match_app`) + Task 3 tests.
- Finding-param `name` keys (`fid`, `tid`, `cid`, `sid`, `check_id`, ..., `evidence_packet`) are consistent across Task 3 (`signin_to_params`) + Task 4 (`_finding_to_params`) + Task 4 (`_FINDING_INSERT_SQL`).
- `tier` values (`personal`/`corp`/`unknown`) consistent across catalog spec, `match_app` return, `_CHECK_BY_TIER`/`_STATUS_BY_TIER`.

**Ambiguity check:**
- "What happens if Graph rate-limits a page?" → spec §9.5 says exponential backoff; plan defers to a "follow-on" since the v1 paginator does one page. Acceptable for a slice that ships visibility — re-scans will catch up. Flagged in Task 3's `_fetch_signins` docstring.
- "What if `last_scan_at` is `None` on the first scan?" → no filter, full page. Plan acceptable but the catalog default `top=1000` means we cap at 1000 events per first scan. Acceptable for v1.
- "What if `userPrincipalName` is null?" → finding still emitted with empty `entra_upn`; the per-person view's `COALESCE(..., entra_upn)` returns null for that row → drops into "Unattributed" via the `IS NOT NULL` filter. Documented in `signin_to_params`.

**Scope check:** Plan is one cohesive sub-slice — single Lambda module + catalog + main.py wiring + verification. ~600 lines of new code total. Self-contained.
