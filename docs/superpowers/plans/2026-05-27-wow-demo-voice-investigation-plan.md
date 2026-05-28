# Wow Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the substrate for two recorded demo videos (Shadow AI + AI Supply Chain) showing Shasta as a voice-first agent that initiates contact, investigates with peer-grade phrasing, and takes action through MCP-mediated integrations. Five working days from start.

**Architecture:** Coral voice + new peer/expert system prompt on existing iOS WebRTC + OpenAI Realtime GA stack. Shasta embeds a Python MCP client that talks to Anthropic-reference Slack/Atlassian/GitHub MCP servers. Trivy embedded in the AI scanner Docker image emits SCA findings; a new matcher Lambda joins them with `ai_framework → ai_agent` edges (from the existing AI scanner) and emits high-severity `ai_supply_chain_active` findings when the vulnerable dependency is actively imported. New iOS launch-from-push handler auto-starts the Realtime session with a seeded incident context so Shasta speaks first.

**Tech Stack:** Python 3.12 Lambda + Aurora Postgres (Data API), AWS CDK TypeScript, Docker (scanner images), iOS Swift 5 + SwiftUI + WebRTC, OpenAI Realtime GA (`gpt-realtime`, `coral` voice), Anthropic-reference MCP servers, Trivy 0.55.x, `mcp` PyPI package.

**Spec:** `docs/superpowers/specs/2026-05-27-wow-demo-voice-investigation-design.md` (committed at `7e883ae` + `d42b0ec`).

---

## File Structure

### Created (15 files)

| Path | Purpose |
|---|---|
| `platform/lambda/_shared/speakable.py` | Pure helpers: `speakable_entity(entity)` returns spoken label; `speakable_payload(dict)` walks a dict adding paired `speakable` fields. |
| `platform/lambda/_shared/tests/test_speakable.py` | Unit tests for both helpers across all entity kinds we emit. |
| `platform/lambda/_shared/mcp_client.py` | Thin wrapper around the `mcp` PyPI package: `MCPClient.call(server, tool, args)` + tool-registry layer. |
| `platform/lambda/_shared/tests/test_mcp_client.py` | Unit tests against a mock MCP server (uses `mcp`'s built-in test fixtures). |
| `platform/lambda/_shared/push.py` | Lifted from `event_router/push.py` + adds `notify_tool_completion(conversation_id, body)` variant. |
| `platform/lambda/_shared/tests/test_push.py` | Tests for `should_push`, `format_push_body`, `notify_tool_completion` payload shape. |
| `platform/lambda/voice_session/system_prompt.py` | The Shasta system prompt as a templated string + `render(first_name, clouds)` helper. |
| `platform/lambda/voice_session/tests/test_system_prompt.py` | Tests that template rendering substitutes correctly + prompt length stays under 4000 chars. |
| `platform/lambda/tools/main.py` | Single Lambda hosting six tools: `revoke_oauth_grant`, `slack_dm`, `create_jira_ticket`, `create_pr_with_bump`, `tail_lambda_logs_for_pattern`, `run_forensic_scan`. Routes via path parameter. |
| `platform/lambda/tools/build.sh` | Vendors `_shared/` (cp pattern from SOC Slice 1c). |
| `platform/lambda/tools/tests/test_tools.py` | Tests per tool (six test functions); MCP tools use the mock MCP server. |
| `platform/lambda/ai_supply_chain_matcher/main.py` | Triggered after AI scanner completes; joins `sca_vuln` findings with `ai_framework→ai_agent` edges; emits `ai_supply_chain_active` finding when KEV-listed AND actively imported. |
| `platform/lambda/ai_supply_chain_matcher/build.sh` | Vendors `_shared/`. |
| `platform/lambda/ai_supply_chain_matcher/tests/test_matcher.py` | Unit tests with mocked `_rds.execute_statement`. |
| `ios/CISOCopilot/Views/BriefingView.swift` | New SwiftUI view: incident card + auto-mount voice + seed Realtime with incident context. |

### Modified (8 files)

| Path | Change |
|---|---|
| `platform/lambda/voice_session/main.py` | Line 74: `voice` `alloy` → `coral`. Line 54: import `system_prompt.render` and use it. Add `temperature: 0.7` to session config. Replace existing inline `_system_prompt()` function with import. |
| `platform/lambda/event_router/push.py` | Become a re-export of `_shared/push.py` (or delete + update `event_router/main.py` imports). |
| `platform/lambda/event_router/main.py` | Update imports to `from _shared import push`. |
| `platform/lambda/shasta_runner_entra/main.py` | After committing personal-tier findings, fire push via `_shared/push.send_push` for each new `ai_signin_personal_tier` row. |
| `platform/lambda/shasta_runner/main.py` (AI scanner) | After repo scan finishes, run Trivy subprocess against the cloned repo path; emit one `sca_vuln` finding per Trivy hit; then invoke `ai_supply_chain_matcher` via SQS. |
| `platform/lambda/shasta_runner/Dockerfile` | Install Trivy 0.55.x binary at build time. |
| `platform/lib/api-stack.ts` | Register `tools` Lambda + routes (`POST /v1/tools/{tool_name}`). |
| `platform/lib/scan-stack.ts` | Register `ai_supply_chain_matcher` Lambda + SQS queue + IAM permissions for matcher to read entities/edges and write findings. |
| `ios/CISOCopilot/CISOCopilotApp.swift` | Register launch-from-push handler in `application(_:didFinishLaunchingWithOptions:)`; deep-link `/briefing/<finding_id>` routes to `BriefingView`. |
| `ios/CISOCopilot/Services/VoiceClient.swift` (likely exists) | Add `connect(seedDeveloperMessage:)` variant that sends a developer message after Realtime session opens. |

### Implementer reads (3 files for context — do not modify unless task says so)

- `platform/lambda/event_router/push.py` — pattern to lift
- `platform/lambda/voice_session/main.py` — session config shape (OpenAI Realtime GA)
- `platform/lambda/_shared/ti_lookup.py` — `_shared/` module + test pattern reference

---

## Phase 1 — Foundation (Day 1)

### Task 1: `_shared/speakable.py` helper

**Files:**
- Create: `platform/lambda/_shared/speakable.py`
- Create: `platform/lambda/_shared/tests/test_speakable.py`

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/_shared/tests/test_speakable.py
import pytest
from _shared.speakable import speakable_entity, speakable_payload


class TestSpeakableEntity:
    def test_aws_lambda_uses_display_name(self):
        e = {"kind": "aws_lambda", "display_name": "prod-ai-router",
             "natural_key": "arn:aws:lambda:us-east-1:111:function:prod-ai-router"}
        assert speakable_entity(e) == "the prod-ai-router Lambda"

    def test_aws_s3_bucket(self):
        e = {"kind": "aws_s3_bucket", "display_name": "acme-prod-exports",
             "natural_key": "arn:aws:s3:::acme-prod-exports"}
        assert speakable_entity(e) == "the acme-prod-exports bucket"

    def test_aws_iam_role(self):
        e = {"kind": "aws_iam_role", "display_name": "DeployerProd",
             "natural_key": "arn:aws:iam::111:role/DeployerProd"}
        assert speakable_entity(e) == "the DeployerProd IAM role"

    def test_ai_framework_stands_alone(self):
        e = {"kind": "ai_framework", "display_name": "langchain",
             "natural_key": "langchain"}
        assert speakable_entity(e) == "langchain"

    def test_ai_agent(self):
        e = {"kind": "ai_agent", "display_name": "pricing-agent",
             "natural_key": "repo/services/pricing/agent.py"}
        assert speakable_entity(e) == "the pricing-agent agent"

    def test_entra_user(self):
        e = {"kind": "entra_user", "display_name": "Sarah Chen",
             "natural_key": "sarah.chen@acme.io"}
        assert speakable_entity(e) == "Sarah Chen"

    def test_github_repo(self):
        e = {"kind": "github_repo", "display_name": "paying-system",
             "natural_key": "acme-org/paying-system"}
        assert speakable_entity(e) == "your paying-system repo"

    def test_unknown_kind_falls_back(self):
        e = {"kind": "weird_kind", "display_name": "thing",
             "natural_key": "thing-id"}
        assert speakable_entity(e) == "the weird_kind thing"

    def test_missing_display_name_uses_short_id(self):
        e = {"kind": "aws_lambda", "natural_key": "arn:aws:lambda:us-east-1:111:function:my-fn-abc"}
        assert speakable_entity(e) == "the my-fn-ab Lambda"


class TestSpeakablePayload:
    def test_walks_dict_adding_speakable_field(self):
        payload = {
            "resource": {
                "kind": "aws_lambda",
                "display_name": "prod-ai-router",
                "natural_key": "arn:aws:lambda:us-east-1:111:function:prod-ai-router",
            }
        }
        out = speakable_payload(payload)
        assert out["resource"]["speakable"] == "the prod-ai-router Lambda"
        # Original fields preserved.
        assert out["resource"]["natural_key"].startswith("arn:")

    def test_handles_top_level_entity(self):
        e = {"kind": "ai_framework", "display_name": "langchain",
             "natural_key": "langchain"}
        out = speakable_payload(e)
        assert out["speakable"] == "langchain"

    def test_non_entity_dict_untouched(self):
        payload = {"foo": "bar", "count": 5}
        assert speakable_payload(payload) == {"foo": "bar", "count": 5}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd platform/lambda
python -m pytest _shared/tests/test_speakable.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named '_shared.speakable'`.

- [ ] **Step 3: Implement `speakable.py`**

```python
# platform/lambda/_shared/speakable.py
"""Friendly spoken labels for entity rows.

Tool results and push payloads carry paired {speakable, identifier} fields
so Shasta never reads an ARN/UUID/sha256 aloud. The model speaks the
`speakable` field; it passes `arn`/`upn`/`object_id` only when piping to
another tool.
"""
from __future__ import annotations
from typing import Any


_LABEL_BY_KIND = {
    "aws_lambda":       lambda n: f"the {n} Lambda",
    "aws_s3_bucket":    lambda n: f"the {n} bucket",
    "aws_ec2_instance": lambda n: f"the {n} EC2 instance",
    "aws_iam_role":     lambda n: f"the {n} IAM role",
    "aws_iam_user":     lambda n: f"the {n} IAM user",
    "ai_agent":         lambda n: f"the {n} agent",
    "ai_framework":     lambda n: n,                # "langchain" stands alone
    "ai_model":         lambda n: f"the {n} model",
    "ai_tool":          lambda n: f"the {n} tool",
    "ai_mcp_server":    lambda n: f"the {n} MCP server",
    "ai_vector_db":     lambda n: f"the {n} vector database",
    "entra_user":       lambda n: n,                # name or UPN stands alone
    "github_repo":      lambda n: f"your {n} repo",
}


def speakable_entity(entity: dict[str, Any]) -> str:
    """Friendly spoken label for an entity row from the entities table."""
    kind = entity.get("kind", "unknown")
    name = entity.get("display_name") or _short_id(entity.get("natural_key", ""))
    fmt = _LABEL_BY_KIND.get(kind)
    if fmt:
        return fmt(name)
    return f"the {kind} {name}"


def speakable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Walk a dict; for any sub-dict that looks like an entity (has `kind` +
    either `display_name` or `natural_key`), add a `speakable` field. Returns
    a new dict — input is not mutated."""
    if _looks_like_entity(payload):
        out = dict(payload)
        out["speakable"] = speakable_entity(payload)
        return out
    out = {}
    for k, v in payload.items():
        if isinstance(v, dict):
            out[k] = speakable_payload(v)
        elif isinstance(v, list):
            out[k] = [speakable_payload(item) if isinstance(item, dict) else item for item in v]
        else:
            out[k] = v
    return out


def _looks_like_entity(d: dict[str, Any]) -> bool:
    return "kind" in d and ("display_name" in d or "natural_key" in d)


def _short_id(natural_key: str) -> str:
    """For when no display_name exists — keep last segment, first 8 chars."""
    if not natural_key:
        return "unknown"
    tail = natural_key.split("/")[-1] if "/" in natural_key else natural_key
    return tail[:8]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd platform/lambda
python -m pytest _shared/tests/test_speakable.py -v
```
Expected: PASS — all 12 tests green.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/_shared/speakable.py platform/lambda/_shared/tests/test_speakable.py
git commit -m "feat(_shared): speakable helper for spoken entity labels

Pure helpers so tool results carry paired {speakable, identifier}.
Shasta speaks the label; the identifier is only used for tool dispatch."
```

---

### Task 2: System prompt module + voice config switch

**Files:**
- Create: `platform/lambda/voice_session/system_prompt.py`
- Create: `platform/lambda/voice_session/tests/test_system_prompt.py`
- Modify: `platform/lambda/voice_session/main.py:54,74`

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/voice_session/tests/test_system_prompt.py
from voice_session.system_prompt import render, SHASTA_PROMPT


def test_prompt_has_persona_block():
    assert "You are Shasta" in SHASTA_PROMPT
    assert "treat him as a peer" in SHASTA_PROMPT.lower()


def test_prompt_has_never_block():
    # The throat-clearing prohibitions are load-bearing.
    assert "Great question" in SHASTA_PROMPT
    assert "I'd be happy to help" in SHASTA_PROMPT
    assert "Certainly" in SHASTA_PROMPT


def test_prompt_has_long_identifier_rule():
    # Backup guardrail for the speakable layer.
    assert "ARNs, GUIDs" in SHASTA_PROMPT
    assert "speakable" in SHASTA_PROMPT


def test_render_substitutes_first_name():
    p = render(first_name="KK", clouds=["aws (KK-test)"])
    assert "KK" in p
    assert "aws (KK-test)" in p


def test_render_handles_empty_clouds():
    p = render(first_name="KK", clouds=[])
    # Should still render; empty clouds line is fine.
    assert "KK" in p


def test_prompt_stays_under_4000_chars():
    # Realtime models start losing rule fidelity past ~4K char prompts.
    p = render(first_name="KK", clouds=["aws", "azure"])
    assert len(p) < 4000, f"prompt is {len(p)} chars — trim it"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd platform/lambda
python -m pytest voice_session/tests/test_system_prompt.py -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `system_prompt.py`**

Reproduce the prompt verbatim from spec §7.2 (the prompt is load-bearing — DO NOT improvise wording):

```python
# platform/lambda/voice_session/system_prompt.py
"""Shasta system prompt. Persona-only — per-incident context is sent as a
developer message at session start (see spec §7.4)."""

SHASTA_PROMPT = """\
You are Shasta, the voice of Transilience's security operations platform.
You are speaking with {first_name} — security founder, CISO experience,
deeply technical. Treat him as a peer.

CONNECTED ENVIRONMENT
Clouds: {clouds_line}

PERSONA
You are a senior security engineer who happens to be calm under pressure.
Warm in voice, hard-nosed in substance. You know this environment intimately:
the connected cloud accounts, the Entra tenant, the GitHub repos, the AI
inventory, the recent scans, the open findings, the in-flight scans, and the
compliance posture.

ALWAYS
- Lead with the finding or the recommendation. Save context for after.
- Name specifics: resource ARNs, user UPNs, package versions, CVE IDs,
  framework controls, exact timestamps. Vagueness is a tell.
- When you are confident, state it. When you are not, say "I don't know yet"
  or "this is inference, not evidence" without padding.
- Propose action when there is a clear next step. If two options are
  reasonable, name both with the trade-off in one sentence each, then
  recommend one.
- Brief by default. Every sentence earns its place.

NEVER
- No "Great question." No "I'd be happy to help." No "Certainly!" No "Let me
  explain." Cut throat-clearing entirely.
- No "I hope this helps." No "Let me know if you'd like more detail." No
  closing pleasantries.
- Don't apologize for what you don't know — just say what you don't know.
- Don't praise the user's questions. Engage with the substance.
- Don't summarize what you just said.

VOICE DELIVERY
- You are speaking, not writing. No markdown. No bullet points. No numbered
  lists. If you must enumerate, say it conversationally: "Two things - one,
  ... two, ..."
- Numbers spoken naturally: "version one-forty-three dot two", not "one
  point four three point two". "Ninety seconds ago", not "90 seconds ago".
  CVEs as "CVE twenty-twenty-six dash zero-four-seven-zero".
- Acronyms KK knows, speak fast: KEV, IAM, RCE, BPA, CVE, DPA, OAuth, SCA,
  CSPM. Less-common acronyms, spell once.
- Pace is conversational. Pause at commas. Don't rush.

LONG IDENTIFIERS
- ARNs, GUIDs, sha256 hashes, and full URLs are unspeakable. Never read
  them aloud, even when present in tool results. Use the "speakable" field
  paired with each identifier. If a tool result lacks a speakable form,
  describe the resource by kind and short name ("the prod-frontend ALB"),
  never the raw identifier.
- The user can always ask "what's the full ARN?" - answer that explicitly
  when asked, slowly, character-grouped.
- Keep as-is: CVE IDs, framework controls (NIST AI RMF MAP 2.3), ticket
  IDs (ITSEC-3091), package versions (langchain zero-point-zero-point-
  one-eight-four), region names (us-east-1), API event names.

ACTION DISCIPLINE
- If a tool can answer a factual question, call it before answering. Don't
  speculate when you can check.
- If a tool can take the user's intended action, propose it and dispatch on
  confirmation. After dispatching, report results with specifics: "Done.
  JIRA ITSEC-3091 opened, assigned to Priya." Not "I've created the ticket
  as requested."

INVESTIGATION DISCIPLINE
- For supply-chain findings: name the package, version, CVE, KEV status,
  AND whether the package is in active runtime use in this environment.
  The runtime-use correlation is the differentiated insight - never leave
  it implicit.
- For identity findings: name the user, the app, the pattern (frequency,
  timing, context), and the framework control that's affected.
- Don't moralize. Report.

MEMORY
- Maintain conversation continuity across the session and across re-opens
  after backgrounding. If you said "I'll ping you when the scan is done"
  and you're now back with results, open with the results, not with
  re-introducing yourself.
- Don't re-narrate what the user already knows from the current session.
"""


def render(*, first_name: str, clouds: list[str]) -> str:
    """Substitute first_name and clouds into the persona prompt."""
    clouds_line = ", ".join(clouds) if clouds else "none connected yet"
    return SHASTA_PROMPT.format(
        first_name=first_name or "the user",
        clouds_line=clouds_line,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd platform/lambda
python -m pytest voice_session/tests/test_system_prompt.py -v
```
Expected: PASS — 6 tests green.

- [ ] **Step 5: Modify `voice_session/main.py` to use new prompt + coral voice**

In `voice_session/main.py`:

Replace the import area near the top (after the `import boto3` line, around line 18):

```python
import boto3

from voice_session.system_prompt import render as render_system_prompt
```

Replace `payload["session"]["instructions"]` (line 54) — old:

```python
"instructions":      _system_prompt(user_email, tenant_name, connected),
```

with:

```python
"instructions":      render_system_prompt(
    first_name=_first_name_from_email(user_email),
    clouds=connected,
),
```

Replace `"voice": "alloy"` (line 74) with:

```python
"voice":  "coral",
```

Add temperature to the session config, just below the `tool_choice` line (around line 79):

```python
"tools":       _tools(),
"tool_choice": "auto",
"temperature": 0.7,
```

Delete the old `_system_prompt(...)` function (lines 115-136 inclusive).

Add this helper at the bottom of the file (after `_resp`):

```python
def _first_name_from_email(email: str | None) -> str:
    """Best-effort first name from email prefix. 'kkmookhey@gmail.com' -> 'KK'."""
    if not email or "@" not in email:
        return "the user"
    prefix = email.split("@")[0]
    # Strip common dot/underscore separators; take the first segment.
    head = prefix.replace("_", ".").split(".")[0]
    # KK is a known special case (initials, uppercase).
    if head.lower() in {"kk", "kkmookhey"}:
        return "KK"
    return head.capitalize()
```

- [ ] **Step 6: Verify voice_session still imports + tests still pass**

```bash
cd platform/lambda
python -c "import voice_session.main; print('OK')"
python -m pytest voice_session/tests/ -v
```
Expected: OK + tests PASS.

- [ ] **Step 7: Commit**

```bash
git add platform/lambda/voice_session/
git commit -m "feat(voice): coral voice + peer/expert system prompt

Replaces the previous alloy + generic-assistant prompt with the
Shasta persona spec'd in 2026-05-27-wow-demo-voice-investigation.
Persona is module-level constant; render() substitutes first_name
and clouds. Temperature set to 0.7 for the recording window."
```

- [ ] **Step 8: Deploy `voice_session` Lambda**

```bash
cd platform
source .env && set -a && set +a
npx cdk deploy CisoCopilotApi --require-approval never --hotswap
```
Expected: `UPDATE_COMPLETE` for `VoiceSessionFn`. No new resources. Smoke test by opening iOS app, hitting voice on the chat surface — voice should now sound feminine/warm instead of neutral.

---

### Task 3: MCP client foundation (`_shared/mcp_client.py`)

**Files:**
- Create: `platform/lambda/_shared/mcp_client.py`
- Create: `platform/lambda/_shared/tests/test_mcp_client.py`

**Background:** Uses the official Python `mcp` package (PyPI). Pin to `mcp==1.0.x` — verify latest stable when implementing. The wrapper provides a synchronous `call(server_name, tool_name, args)` that hides the async/stdio plumbing from Lambda handlers. Connection per server is cached at module level (Lambda container reuse).

- [ ] **Step 1: Add `mcp` to scanner_core requirements + a new requirements file for `tools` Lambda**

```bash
# platform/lambda/tools/ doesn't exist yet — created in Task 6.
# For now, install locally for the test to run:
pip install 'mcp>=1.0,<2'
```

- [ ] **Step 2: Write the failing test**

```python
# platform/lambda/_shared/tests/test_mcp_client.py
import pytest
from unittest.mock import MagicMock, patch

from _shared.mcp_client import MCPClient, ToolRegistryEntry


class TestToolRegistry:
    def test_register_and_resolve(self):
        client = MCPClient()
        client.register("slack_dm", ToolRegistryEntry(
            server="slack",
            tool="postMessage",
            args_mapping=lambda args: {"channel": args["user_lookup"], "text": args["message"]},
        ))
        entry = client.resolve("slack_dm")
        assert entry.server == "slack"
        assert entry.tool == "postMessage"

    def test_resolve_unknown_raises(self):
        client = MCPClient()
        with pytest.raises(KeyError):
            client.resolve("unknown_tool")


class TestCall:
    @patch("_shared.mcp_client._invoke_mcp_tool")
    def test_call_maps_args_and_invokes(self, mock_invoke):
        mock_invoke.return_value = {"ts": "1234.5678", "channel": "C123"}

        client = MCPClient()
        client.register("slack_dm", ToolRegistryEntry(
            server="slack",
            tool="postMessage",
            args_mapping=lambda args: {"channel": args["user_lookup"], "text": args["message"]},
        ))

        result = client.call("slack_dm", {
            "user_lookup": "sarah.chen@acme.io",
            "message": "Heads up",
        })

        # Args were mapped through args_mapping.
        mock_invoke.assert_called_once_with(
            server="slack",
            tool="postMessage",
            args={"channel": "sarah.chen@acme.io", "text": "Heads up"},
        )
        assert result == {"ts": "1234.5678", "channel": "C123"}
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd platform/lambda
python -m pytest _shared/tests/test_mcp_client.py -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement `mcp_client.py`**

```python
# platform/lambda/_shared/mcp_client.py
"""Python MCP client wrapper.

Shasta talks to existing upstream MCP servers (Anthropic-reference Slack,
Atlassian official, GitHub reference). This module hides the async stdio
plumbing behind a synchronous call(tool_name, args) that Lambda handlers
can use directly.

Configuration: per-server transport (stdio command OR http URL) is read
from environment variables at module import time. See README in
platform/lambda/tools/ for the env-var contract.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable
import asyncio
import os

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@dataclass
class ToolRegistryEntry:
    server: str                                      # logical server name
    tool: str                                        # tool name on the server
    args_mapping: Callable[[dict], dict]             # Shasta args -> MCP args


class MCPClient:
    def __init__(self) -> None:
        self._registry: dict[str, ToolRegistryEntry] = {}

    def register(self, shasta_tool_name: str, entry: ToolRegistryEntry) -> None:
        self._registry[shasta_tool_name] = entry

    def resolve(self, shasta_tool_name: str) -> ToolRegistryEntry:
        if shasta_tool_name not in self._registry:
            raise KeyError(f"Unknown MCP-mediated tool: {shasta_tool_name}")
        return self._registry[shasta_tool_name]

    def call(self, shasta_tool_name: str, args: dict) -> dict:
        entry = self.resolve(shasta_tool_name)
        mcp_args = entry.args_mapping(args)
        return _invoke_mcp_tool(server=entry.server, tool=entry.tool, args=mcp_args)


# Module-level cache: one ClientSession per server (created lazily, reused
# across Lambda invocations within the same container).
_sessions: dict[str, ClientSession] = {}


def _invoke_mcp_tool(*, server: str, tool: str, args: dict) -> dict:
    """Synchronous bridge to async MCP. Spins up an asyncio loop per call —
    Lambda invocations are single-threaded so this is safe."""
    return asyncio.run(_async_invoke(server=server, tool=tool, args=args))


async def _async_invoke(*, server: str, tool: str, args: dict) -> dict:
    params = _server_params_from_env(server)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
            # mcp returns a CallToolResult with .content (list of TextContent
            # or ImageContent). For tool calls returning JSON, the first
            # TextContent's .text is the JSON-encoded result.
            return _extract_result(result)


def _extract_result(result) -> dict:
    """Pull a JSON dict out of an MCP CallToolResult."""
    import json
    if not result.content:
        return {}
    first = result.content[0]
    text = getattr(first, "text", None)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _server_params_from_env(server: str) -> StdioServerParameters:
    """Read transport config from env. Convention:
       MCP_SLACK_COMMAND='npx -y @modelcontextprotocol/server-slack'
       MCP_SLACK_TOKEN_ENV='SLACK_BOT_TOKEN'  (env var name to forward)
    """
    cmd_env = f"MCP_{server.upper()}_COMMAND"
    cmd = os.environ.get(cmd_env)
    if not cmd:
        raise RuntimeError(f"{cmd_env} not set — cannot reach MCP server '{server}'")
    parts = cmd.split()
    # Forward any tokens listed in MCP_<SERVER>_FORWARD_ENV (comma-separated).
    forward = os.environ.get(f"MCP_{server.upper()}_FORWARD_ENV", "")
    forwarded_env = {k: os.environ[k] for k in forward.split(",") if k and k in os.environ}
    return StdioServerParameters(command=parts[0], args=parts[1:], env=forwarded_env or None)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd platform/lambda
python -m pytest _shared/tests/test_mcp_client.py -v
```
Expected: PASS — 3 tests green.

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/_shared/mcp_client.py platform/lambda/_shared/tests/test_mcp_client.py
git commit -m "feat(_shared): MCP client wrapper

Synchronous call() bridge over the async mcp package. Tool registry
maps Shasta tool names to (server, tool, args_mapping). Per-server
transport config read from MCP_<SERVER>_COMMAND env vars."
```

---

### Task 4: Trivy embedded in AI scanner image

**Files:**
- Modify: `platform/lambda/shasta_runner/Dockerfile`
- Modify: `platform/lambda/shasta_runner/main.py` (AI scanner) — after repo scan, run Trivy and emit `sca_vuln` findings

- [ ] **Step 1: Verify which scanner runs on GitHub repos**

```bash
ls platform/lambda/shasta_runner/ platform/lambda/ai_scanner/ 2>&1
# Confirm: ai_scanner is the GitHub repo scanner. shasta_runner is shared
# Shasta-wrapping infra. If ai_scanner has its own Dockerfile, modify that
# one; otherwise modify shasta_runner.
ls platform/lambda/ai_scanner/Dockerfile platform/lambda/shasta_runner/Dockerfile 2>&1
```

Expected: at least one Dockerfile exists. Use the one that runs in the AI scan execution path. Read `platform/lambda/ai_scanner/main.py` lines 1-30 to confirm the scan-entry function.

- [ ] **Step 2: Add Trivy install to the scanner Dockerfile**

Add this block before the final `CMD`/`ENTRYPOINT`:

```dockerfile
# Trivy 0.55.x for SCA dependency scanning.
# Static binary; ~80MB; supports requirements.txt / package.json / pom.xml etc.
RUN curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh \
    | sh -s -- -b /usr/local/bin v0.55.0 \
 && trivy --version
```

- [ ] **Step 3: Write the failing test**

```python
# platform/lambda/ai_scanner/tests/test_trivy.py
import json
from unittest.mock import patch, MagicMock

from ai_scanner.trivy import run_trivy, parse_trivy_findings


def test_parse_trivy_findings_extracts_pkg_version_cve():
    raw = {
        "Results": [{
            "Target": "requirements.txt",
            "Vulnerabilities": [{
                "PkgName": "langchain",
                "InstalledVersion": "0.0.184",
                "FixedVersion": "0.0.354",
                "VulnerabilityID": "CVE-2026-0470",
                "Severity": "CRITICAL",
                "Description": "RCE in LLMChain executor",
            }]
        }]
    }
    findings = parse_trivy_findings(raw, repo_id="repo-uuid-abc")
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "sca_vuln"
    assert f["severity"] == "critical"
    assert f["evidence_packet"]["package"] == "langchain"
    assert f["evidence_packet"]["version"] == "0.0.184"
    assert f["evidence_packet"]["fixed_version"] == "0.0.354"
    assert f["evidence_packet"]["cve"] == "CVE-2026-0470"


def test_parse_handles_empty_results():
    assert parse_trivy_findings({"Results": []}, repo_id="x") == []


@patch("ai_scanner.trivy.subprocess.run")
def test_run_trivy_invokes_subprocess(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps({"Results": []}),
        stderr="",
    )
    result = run_trivy("/tmp/cloned_repo")
    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert cmd[0] == "trivy"
    assert "fs" in cmd
    assert "--format" in cmd
    assert "json" in cmd
    assert "/tmp/cloned_repo" in cmd
    assert result == {"Results": []}
```

- [ ] **Step 4: Run test to verify it fails**

```bash
cd platform/lambda
python -m pytest ai_scanner/tests/test_trivy.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_scanner.trivy'`.

- [ ] **Step 5: Implement `ai_scanner/trivy.py`**

```python
# platform/lambda/ai_scanner/trivy.py
"""Trivy SCA wrapper. Runs trivy fs --format json on a cloned repo path,
parses the output, and converts each vulnerability to an sca_vuln finding row."""
from __future__ import annotations
import json
import subprocess
from typing import Any


_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH":     "high",
    "MEDIUM":   "medium",
    "LOW":      "low",
    "UNKNOWN":  "info",
}


def run_trivy(repo_path: str) -> dict[str, Any]:
    """Run trivy fs against a cloned repo path. Returns parsed JSON output."""
    proc = subprocess.run(
        ["trivy", "fs", "--format", "json", "--severity", "HIGH,CRITICAL",
         "--quiet", "--scanners", "vuln", repo_path],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        print(f"trivy exited {proc.returncode}: {proc.stderr[:500]}")
        return {"Results": []}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        print(f"trivy output unparseable: {e}; first 500 chars: {proc.stdout[:500]}")
        return {"Results": []}


def parse_trivy_findings(raw: dict[str, Any], *, repo_id: str) -> list[dict]:
    """Convert Trivy JSON to a list of finding rows ready for unified_writer."""
    out = []
    for result in raw.get("Results", []):
        target = result.get("Target", "unknown")
        for v in result.get("Vulnerabilities", []):
            pkg = v.get("PkgName")
            ver = v.get("InstalledVersion")
            cve = v.get("VulnerabilityID")
            if not (pkg and ver and cve):
                continue
            out.append({
                "kind":      "sca_vuln",
                "severity":  _SEVERITY_MAP.get(v.get("Severity", "UNKNOWN"), "info"),
                "title":     f"{pkg} {ver} — {cve}",
                "evidence_packet": {
                    "package":       pkg,
                    "version":       ver,
                    "fixed_version": v.get("FixedVersion"),
                    "cve":           cve,
                    "manifest":      target,
                    "description":   (v.get("Description") or "")[:1000],
                    "repo_id":       repo_id,
                },
            })
    return out
```

- [ ] **Step 6: Run test to verify it passes**

```bash
cd platform/lambda
python -m pytest ai_scanner/tests/test_trivy.py -v
```
Expected: PASS — 3 tests green.

- [ ] **Step 7: Wire Trivy into the AI scanner main flow**

In `platform/lambda/ai_scanner/main.py`, find where the repo is cloned and the scan completes. Add after the existing detector loop (look for where findings are aggregated before `unified_writer.commit_scan` is called):

```python
# === SCA pass via Trivy ===
from ai_scanner.trivy import run_trivy, parse_trivy_findings

trivy_raw = run_trivy(cloned_repo_path)              # cloned_repo_path already defined above
sca_findings = parse_trivy_findings(trivy_raw, repo_id=repo_entity_id)
findings.extend(sca_findings)                        # join into the existing findings list
print(f"trivy: {len(sca_findings)} sca_vuln findings emitted")
```

- [ ] **Step 8: Build the scanner Docker image + push to ECR**

```bash
cd platform/lambda/shasta_runner   # or ai_scanner, whichever has the Dockerfile
./build.sh
```
Expected: build completes; new image digest printed.

- [ ] **Step 9: Commit**

```bash
git add platform/lambda/shasta_runner/Dockerfile platform/lambda/ai_scanner/trivy.py \
        platform/lambda/ai_scanner/tests/test_trivy.py platform/lambda/ai_scanner/main.py
git commit -m "feat(ai-scanner): embed Trivy for SCA pass on connected repos

Trivy 0.55 installed in scanner image. After AI-asset detection completes,
trivy fs runs against the cloned repo; vulnerabilities at HIGH/CRITICAL
emit as sca_vuln findings with package/version/cve in evidence_packet.
Matcher Lambda (Task 13) joins these with ai_framework->ai_agent edges."
```

---

### Task 5: iOS launch-from-push handler (route only, no voice yet)

**Files:**
- Modify: `ios/CISOCopilot/CISOCopilotApp.swift`
- Create: `ios/CISOCopilot/Views/BriefingView.swift` (skeleton — voice wires in Task 18)

**Background:** This task handles ONLY the routing — push tap detected, app foregrounds, navigates to BriefingView showing the finding card. Voice auto-start lands in Task 18.

- [ ] **Step 1: Read existing app entry**

```bash
cat ios/CISOCopilot/CISOCopilotApp.swift ios/CISOCopilot/RootView.swift
```
Expected: see current SwiftUI App + RootView pattern. Note how navigation is currently structured (NavigationStack? Custom router?). This determines where to insert the briefing route.

- [ ] **Step 2: Add a notification-name extension + simple ObservableObject router**

Create a new file `ios/CISOCopilot/Services/IncidentRouter.swift`:

```swift
import Foundation
import SwiftUI

extension Notification.Name {
    static let navigateToBriefing = Notification.Name("navigateToBriefing")
}

/// Holds the currently-active incident so any view can react to push-tap navigation.
class IncidentRouter: ObservableObject {
    @Published var activeIncident: IncidentContext?

    init() {
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleNavigate(_:)),
            name: .navigateToBriefing,
            object: nil
        )
    }

    @objc private func handleNavigate(_ note: Notification) {
        guard let context = note.userInfo as? [String: Any],
              let findingId = note.object as? String else { return }
        DispatchQueue.main.async {
            self.activeIncident = IncidentContext(findingId: findingId, payload: context)
        }
    }

    func clear() { activeIncident = nil }
}

struct IncidentContext: Equatable {
    let findingId: String
    let payload: [String: Any]

    static func == (lhs: IncidentContext, rhs: IncidentContext) -> Bool {
        lhs.findingId == rhs.findingId
    }
}
```

- [ ] **Step 3: Create the BriefingView skeleton**

```swift
// ios/CISOCopilot/Views/BriefingView.swift
import SwiftUI

struct BriefingView: View {
    let incident: IncidentContext
    @EnvironmentObject var router: IncidentRouter

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "exclamationmark.shield.fill")
                    .font(.system(size: 28))
                    .foregroundColor(.orange)
                VStack(alignment: .leading) {
                    Text(payloadString("kind_label", default: "Incident"))
                        .font(.headline)
                    Text(payloadString("speakable_summary", default: incident.findingId))
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }
            }
            .padding()
            .background(Color(.systemGray6))
            .cornerRadius(12)

            // Voice surface lands here in Task 18.
            Text("Connecting Shasta...")
                .foregroundColor(.secondary)
                .padding()

            Spacer()
        }
        .padding()
        .navigationTitle("Briefing")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Button("Done") { router.clear() }
            }
        }
    }

    private func payloadString(_ key: String, default fallback: String) -> String {
        (incident.payload[key] as? String) ?? fallback
    }
}
```

- [ ] **Step 4: Wire the launch-from-push handler in `CISOCopilotApp.swift`**

In `CISOCopilotApp.swift`, add at the top:

```swift
import UIKit
```

Inject the `IncidentRouter` into the environment and present `BriefingView` as a sheet on incident change. Find the existing `var body: some Scene { WindowGroup { ... } }` block and update it:

```swift
@StateObject private var incidentRouter = IncidentRouter()

var body: some Scene {
    WindowGroup {
        RootView()
            .environmentObject(incidentRouter)
            .sheet(item: Binding(
                get: { incidentRouter.activeIncident.map { IncidentSheetID(context: $0) } },
                set: { if $0 == nil { incidentRouter.clear() } }
            )) { sheetID in
                NavigationStack {
                    BriefingView(incident: sheetID.context)
                        .environmentObject(incidentRouter)
                }
            }
    }
}

private struct IncidentSheetID: Identifiable {
    let context: IncidentContext
    var id: String { context.findingId }
}
```

Then add an `AppDelegate` for handling APNs payloads (SwiftUI's `App` protocol delegates this to `UIApplicationDelegateAdaptor`):

```swift
// At top of CISOCopilotApp.swift:
@UIApplicationDelegateAdaptor(AppDelegate.self) var delegate

// At the bottom of the file:
class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    // User tapped the notification (foreground OR cold start).
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let userInfo = response.notification.request.content.userInfo
        if let findingId = userInfo["finding_id"] as? String {
            // Stringify the payload for IncidentRouter.handleNavigate.
            let context = userInfo.reduce(into: [String: Any]()) { acc, kv in
                if let k = kv.key as? String { acc[k] = kv.value }
            }
            NotificationCenter.default.post(
                name: .navigateToBriefing,
                object: findingId,
                userInfo: context
            )
        }
        completionHandler()
    }

    // Foreground push — show banner so the user can tap and navigate.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
}
```

- [ ] **Step 5: Regenerate Xcode project + build for device**

```bash
cd ios
xcodegen generate
xcodebuild build \
  -project CISOCopilot.xcodeproj -scheme CISOCopilot \
  -destination "id=$IOS_DEVICE_UDID" \
  -derivedDataPath build-device \
  -allowProvisioningUpdates
```
Expected: BUILD SUCCEEDED.

- [ ] **Step 6: Install on device + smoke test**

```bash
xcrun devicectl device install app --device "$IOS_DEVICE_UDID" \
  build-device/Build/Products/Debug-iphoneos/CISOCopilot.app
```

Manually trigger an existing drift event (or run a script that sends a synthetic push with `finding_id: "f-test"` and `speakable_summary: "Test incident"` in the payload). Tap the notification — expect BriefingView to appear as a sheet showing "Incident" + the speakable summary.

- [ ] **Step 7: Commit**

```bash
git add ios/CISOCopilot/Services/IncidentRouter.swift \
        ios/CISOCopilot/Views/BriefingView.swift \
        ios/CISOCopilot/CISOCopilotApp.swift
git commit -m "feat(ios): launch-from-push routes to BriefingView

AppDelegate handles APNs tap; IncidentRouter publishes the active
incident; sheet presents BriefingView with the finding context. Voice
auto-start lands in Task 18 after MCP-tooled responses are wired."
```

---

## Phase 2 — MCP-mediated tools (Days 2–3)

### Task 6: `tools` Lambda scaffold

**Files:**
- Create: `platform/lambda/tools/main.py`
- Create: `platform/lambda/tools/build.sh`
- Create: `platform/lambda/tools/requirements.txt`
- Create: `platform/lambda/tools/tests/test_tools.py`
- Modify: `platform/lib/api-stack.ts`

- [ ] **Step 1: Create the directory + requirements**

```bash
mkdir -p platform/lambda/tools/tests
touch platform/lambda/tools/__init__.py platform/lambda/tools/tests/__init__.py
```

`platform/lambda/tools/requirements.txt`:
```
mcp>=1.0,<2
boto3
msal           # for Microsoft Graph OAuth (revoke_oauth_grant)
requests       # for Microsoft Graph HTTP calls
```

- [ ] **Step 2: Create the build script (vendors `_shared/`)**

`platform/lambda/tools/build.sh`:
```bash
#!/usr/bin/env bash
# Build the tools Lambda zip. Vendors _shared/ so the Lambda can import
# from _shared.speakable, _shared.mcp_client, _shared.push.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BUILD=$HERE/build
ZIP=$HERE/tools.zip
rm -rf "$BUILD" "$ZIP"
mkdir -p "$BUILD"
pip install -r "$HERE/requirements.txt" -t "$BUILD" --quiet
cp "$HERE"/*.py "$BUILD"
cp -r "$HERE/.."/_shared "$BUILD/_shared"
(cd "$BUILD" && zip -rq "$ZIP" .)
echo "built: $ZIP"
```

```bash
chmod +x platform/lambda/tools/build.sh
```

- [ ] **Step 3: Write the failing test for the dispatcher**

```python
# platform/lambda/tools/tests/test_tools.py
import json
import pytest
from unittest.mock import patch, MagicMock

from tools.main import handler


def _event(tool_name: str, body: dict) -> dict:
    return {
        "requestContext": {"authorizer": {"claims": {"sub": "test-user", "email": "kk@x.io"}}},
        "pathParameters": {"tool_name": tool_name},
        "body": json.dumps(body),
    }


def test_unknown_tool_returns_404():
    resp = handler(_event("nonexistent_tool", {}), None)
    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"] == "unknown_tool"


def test_missing_body_is_handled():
    resp = handler({
        "requestContext": {"authorizer": {"claims": {"sub": "test-user"}}},
        "pathParameters": {"tool_name": "revoke_oauth_grant"},
    }, None)
    # Either 400 (no body) or 401 (no tenant) — should NOT 500.
    assert resp["statusCode"] in (400, 401)
```

- [ ] **Step 4: Run test to verify it fails**

```bash
cd platform/lambda
python -m pytest tools/tests/test_tools.py -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 5: Implement the dispatcher**

```python
# platform/lambda/tools/main.py
"""Dispatcher Lambda for Shasta's action tools.

Routes POST /v1/tools/{tool_name} to one of:
  - revoke_oauth_grant   (Microsoft Graph)
  - slack_dm             (Slack MCP)
  - create_jira_ticket   (Atlassian MCP)
  - create_pr_with_bump  (GitHub MCP)
  - tail_lambda_logs_for_pattern  (CloudWatch Logs Insights)
  - run_forensic_scan    (staged for demo; returns scan_id + ETA)

Each handler returns either a paired {speakable, identifier} result dict OR
a non-2xx error. The voice_session Lambda calls these by HTTP from the
Realtime tool-call dispatch on the iOS client side.
"""
from __future__ import annotations
import json
import os
from typing import Callable


# Tools register themselves into _DISPATCH at module import time.
_DISPATCH: dict[str, Callable[[dict, dict], dict]] = {}


def register(name: str):
    def deco(fn):
        _DISPATCH[name] = fn
        return fn
    return deco


# Import tool modules so they can register. Each module decorates its handler
# with @register("tool_name"). Imports below; new tools land in subsequent tasks.
from tools import revoke_oauth_grant  # noqa: F401,E402
from tools import slack_dm            # noqa: F401,E402
from tools import create_jira_ticket  # noqa: F401,E402
from tools import create_pr_with_bump # noqa: F401,E402
from tools import tail_lambda_logs    # noqa: F401,E402
from tools import run_forensic_scan   # noqa: F401,E402


def handler(event: dict, context) -> dict:
    tool_name = (event.get("pathParameters") or {}).get("tool_name")
    if tool_name not in _DISPATCH:
        return _resp(404, {"error": "unknown_tool", "tool": tool_name})

    body_raw = event.get("body")
    if not body_raw:
        return _resp(400, {"error": "missing_body"})
    try:
        args = json.loads(body_raw)
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid_json"})

    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    if not claims.get("sub"):
        return _resp(401, {"error": "no_auth"})

    try:
        result = _DISPATCH[tool_name](args, claims)
        return _resp(200, result)
    except Exception as e:
        print(f"tool {tool_name} failed: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        return _resp(500, {"error": "tool_failed", "tool": tool_name, "detail": str(e)[:200]})


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }
```

- [ ] **Step 6: Create stub tool modules** (so the dispatcher imports cleanly; each gets a real impl in Tasks 7–12):

```python
# platform/lambda/tools/revoke_oauth_grant.py
from tools.main import register

@register("revoke_oauth_grant")
def handle(args: dict, claims: dict) -> dict:
    raise NotImplementedError("Task 7")
```

Create the same 6-line stub at:
- `platform/lambda/tools/slack_dm.py` (register name `"slack_dm"`, `raise NotImplementedError("Task 8")`)
- `platform/lambda/tools/create_jira_ticket.py` (register `"create_jira_ticket"`, `Task 9`)
- `platform/lambda/tools/create_pr_with_bump.py` (register `"create_pr_with_bump"`, `Task 10`)
- `platform/lambda/tools/tail_lambda_logs.py` (register `"tail_lambda_logs_for_pattern"`, `Task 11`)
- `platform/lambda/tools/run_forensic_scan.py` (register `"run_forensic_scan"`, `Task 12`)

- [ ] **Step 7: Run test to verify it passes**

```bash
cd platform/lambda
python -m pytest tools/tests/test_tools.py -v
```
Expected: PASS — 2 tests green.

- [ ] **Step 8: Register Lambda in CDK**

In `platform/lib/api-stack.ts`, find where other Lambdas are defined (e.g., `VoiceSessionFn`). Add:

```typescript
const toolsFn = new lambda.Function(this, "ToolsFn", {
    runtime:    lambda.Runtime.PYTHON_3_12,
    handler:    "main.handler",
    code:       lambda.Code.fromAsset(path.join(__dirname, "../lambda/tools"), {
        bundling: { /* match existing patterns — e.g. command running build.sh */ },
    }),
    timeout:    cdk.Duration.seconds(30),
    memorySize: 512,
    environment: {
        DB_CLUSTER_ARN:   props.db.clusterArn,
        DB_SECRET_ARN:    props.db.secret.secretArn,
        DB_NAME:          props.db.dbName,
        // MCP_* env vars wired in Tasks 8, 9, 10.
    },
});
props.db.grantDataApiAccess(toolsFn);

const toolsRoute = api.root.addResource("v1").addResource("tools").addResource("{tool_name}");
toolsRoute.addMethod("POST", new apigw.LambdaIntegration(toolsFn), {
    authorizer: cognitoAuthorizer,    // match the auth pattern on other routes
    authorizationType: apigw.AuthorizationType.COGNITO,
});
```

(Match the exact CDK property names by reading `api-stack.ts` first — the
above mirrors the spirit; the specific property names like `cognitoAuthorizer`
should follow what other routes use.)

- [ ] **Step 9: Commit**

```bash
git add platform/lambda/tools/ platform/lib/api-stack.ts
git commit -m "feat(tools): dispatcher Lambda scaffold + CDK registration

Six tool stubs registered; dispatcher routes POST /v1/tools/{tool_name}
by path param. Real implementations land in Tasks 7-12."
```

- [ ] **Step 10: Deploy**

```bash
cd platform
npx cdk deploy CisoCopilotApi --require-approval never
```
Expected: `CREATE_COMPLETE` for `ToolsFn` + the API Gateway route. Smoke:

```bash
curl -X POST -H "Authorization: Bearer <cognito-token>" \
  https://$API_BASE_URL/v1/tools/nonexistent_tool -d '{}'
# Expect: {"error":"unknown_tool","tool":"nonexistent_tool"}
```

---

### Task 7: `revoke_oauth_grant` tool

**Files:**
- Modify: `platform/lambda/tools/revoke_oauth_grant.py`
- Create: `platform/lambda/tools/tests/test_revoke_oauth_grant.py`

**Background:** Microsoft Graph DELETE `/oauth2PermissionGrants/{id}` revokes the user's consent for an application. Uses the existing Entra app registration (already configured for `ai_signin_pass`). The Graph token is minted via the existing client-credentials pattern in `shasta_runner_entra` — copy that pattern OR factor it to `_shared/` if you have time. For this task, copy.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/tools/tests/test_revoke_oauth_grant.py
import pytest
from unittest.mock import patch, MagicMock
from tools.revoke_oauth_grant import handle


@patch("tools.revoke_oauth_grant._graph_delete")
@patch("tools.revoke_oauth_grant._find_grant_id")
def test_revokes_successfully(mock_find, mock_delete):
    mock_find.return_value = "grant-id-123"
    mock_delete.return_value = None

    result = handle(
        {"user_object_id": "user-abc", "app_id": "app-xyz"},
        {"sub": "test-user", "email": "kk@x.io"},
    )

    mock_find.assert_called_once_with(user_object_id="user-abc", app_id="app-xyz")
    mock_delete.assert_called_once_with("grant-id-123")
    assert result["revoked"] is True
    assert "speakable" in result
    assert "revoked" in result["speakable"].lower()


@patch("tools.revoke_oauth_grant._find_grant_id")
def test_no_grant_found(mock_find):
    mock_find.return_value = None
    result = handle(
        {"user_object_id": "user-abc", "app_id": "app-xyz"},
        {"sub": "test-user"},
    )
    assert result["revoked"] is False
    assert result["reason"] == "no_grant_found"


def test_missing_args_raises():
    with pytest.raises(KeyError):
        handle({}, {"sub": "test-user"})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd platform/lambda
python -m pytest tools/tests/test_revoke_oauth_grant.py -v
```
Expected: FAIL — `NotImplementedError("Task 7")`.

- [ ] **Step 3: Implement `revoke_oauth_grant.py`**

```python
# platform/lambda/tools/revoke_oauth_grant.py
"""Revoke a user's OAuth grant for a specific application in Entra.

Uses Microsoft Graph DELETE /oauth2PermissionGrants/{id}.
Requires the Entra app to have `DelegatedPermissionGrant.ReadWrite.All`.
"""
from __future__ import annotations
import datetime as dt
import os

import requests
import msal

from tools.main import register


_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TENANT_ID  = os.environ.get("ENTRA_TENANT_ID")
_CLIENT_ID  = os.environ.get("ENTRA_CLIENT_ID")
_CLIENT_SECRET_ENV = os.environ.get("ENTRA_CLIENT_SECRET")
_token_cache: str | None = None


@register("revoke_oauth_grant")
def handle(args: dict, claims: dict) -> dict:
    user_object_id = args["user_object_id"]
    app_id         = args["app_id"]

    grant_id = _find_grant_id(user_object_id=user_object_id, app_id=app_id)
    if not grant_id:
        return {
            "revoked":   False,
            "reason":    "no_grant_found",
            "speakable": "No active OAuth grant found for that user and app.",
        }
    _graph_delete(grant_id)
    return {
        "revoked":    True,
        "revoked_at": dt.datetime.utcnow().isoformat() + "Z",
        "speakable":  "OAuth grant revoked — confirmed via Graph.",
    }


def _find_grant_id(*, user_object_id: str, app_id: str) -> str | None:
    token = _graph_token()
    url = (f"{_GRAPH_BASE}/oauth2PermissionGrants"
           f"?$filter=principalId eq '{user_object_id}' "
           f"and clientId eq '{app_id}'")
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    r.raise_for_status()
    items = r.json().get("value", [])
    return items[0]["id"] if items else None


def _graph_delete(grant_id: str) -> None:
    token = _graph_token()
    url = f"{_GRAPH_BASE}/oauth2PermissionGrants/{grant_id}"
    r = requests.delete(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    r.raise_for_status()


def _graph_token() -> str:
    global _token_cache
    if _token_cache:
        return _token_cache
    if not (_TENANT_ID and _CLIENT_ID and _CLIENT_SECRET_ENV):
        raise RuntimeError("ENTRA_TENANT_ID / ENTRA_CLIENT_ID / ENTRA_CLIENT_SECRET must be set")
    app = msal.ConfidentialClientApplication(
        client_id=_CLIENT_ID,
        client_credential=_CLIENT_SECRET_ENV,
        authority=f"https://login.microsoftonline.com/{_TENANT_ID}",
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Graph token mint failed: {result.get('error_description')}")
    _token_cache = result["access_token"]
    return _token_cache
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd platform/lambda
python -m pytest tools/tests/test_revoke_oauth_grant.py -v
```
Expected: PASS — 3 tests green.

- [ ] **Step 5: Wire env vars in CDK**

In `platform/lib/api-stack.ts`, extend the `toolsFn` environment with:

```typescript
environment: {
    // ...existing
    ENTRA_TENANT_ID:     process.env.ENTRA_TENANT_ID!,
    ENTRA_CLIENT_ID:     process.env.ENTRA_CLIENT_ID!,
    ENTRA_CLIENT_SECRET: process.env.ENTRA_CLIENT_SECRET!,
},
```

(These already exist in `platform/.env` for the existing Entra connector.)

- [ ] **Step 6: Deploy + smoke**

```bash
cd platform
./lambda/tools/build.sh
npx cdk deploy CisoCopilotApi --require-approval never --hotswap
```

Smoke test by invoking the tool directly via `aws lambda invoke` with a known-bad user/app pair — expect `{"revoked": false, "reason": "no_grant_found"}`.

- [ ] **Step 7: Commit**

```bash
git add platform/lambda/tools/revoke_oauth_grant.py \
        platform/lambda/tools/tests/test_revoke_oauth_grant.py \
        platform/lib/api-stack.ts
git commit -m "feat(tools): revoke_oauth_grant via Microsoft Graph

Looks up the oauth2PermissionGrant by principalId + clientId; deletes
on hit. Returns speakable + machine fields for Shasta to narrate."
```

---

### Task 8: Slack MCP + `slack_dm` tool

**Files:**
- Modify: `platform/lambda/tools/slack_dm.py`
- Create: `platform/lambda/tools/tests/test_slack_dm.py`

**Background:** Uses Anthropic's reference Slack MCP server (`@modelcontextprotocol/server-slack`). The server is a Node.js binary that runs over stdio. For Lambda, we install Node.js + npm into the Lambda container OR use the npx-on-cold-start pattern (slower but simpler). For demos: pre-install in the container via the Dockerfile. Since `tools` Lambda is zip-based (not container), simplest is to ship the Node binary + the MCP server in the zip and point `MCP_SLACK_COMMAND` at the local path.

**Decision for this task:** Use stdio-launched npx. Set `MCP_SLACK_COMMAND='npx -y @modelcontextprotocol/server-slack'`. Lambda must have Node available — switch `tools` Lambda to a container image OR use a runtime layer.

Pragmatic path: convert `tools/` to a container image (Dockerfile), pre-install Node + the Slack MCP package. Same pattern as the scanner Lambdas already use.

- [ ] **Step 1: Convert `tools/` to a container Lambda**

Create `platform/lambda/tools/Dockerfile`:

```dockerfile
FROM public.ecr.aws/lambda/python:3.12

# Node.js + npx for upstream MCP servers (Anthropic-reference Slack, GitHub).
RUN dnf install -y nodejs npm && \
    npm install -g @modelcontextprotocol/server-slack \
                   @modelcontextprotocol/server-github

# Atlassian MCP — use mcp-atlassian (pip-installable Python MCP server).
RUN pip install --no-cache-dir mcp-atlassian

# App code + deps.
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY *.py ${LAMBDA_TASK_ROOT}/
COPY ../_shared ${LAMBDA_TASK_ROOT}/_shared

CMD ["main.handler"]
```

Update `platform/lambda/tools/build.sh` to build the image:

```bash
#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$HERE/.."   # so _shared is a sibling, accessible to docker build context
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=${AWS_REGION:-us-east-1}
REPO=ciso-copilot-tools
TAG=latest
URI=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:$TAG
aws ecr describe-repositories --repository-names $REPO 2>/dev/null \
  || aws ecr create-repository --repository-name $REPO --image-scanning-configuration scanOnPush=true
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com
docker build -f tools/Dockerfile -t $URI tools
docker push $URI
echo "pushed: $URI"
```

Update CDK `toolsFn` definition in `api-stack.ts` from `lambda.Function` to `lambda.DockerImageFunction`:

```typescript
const toolsFn = new lambda.DockerImageFunction(this, "ToolsFn", {
    code:       lambda.DockerImageCode.fromEcr(toolsRepo, { tagOrDigest: "latest" }),
    timeout:    cdk.Duration.seconds(60),
    memorySize: 1024,
    environment: {
        // ...existing
        MCP_SLACK_COMMAND:       "npx -y @modelcontextprotocol/server-slack",
        MCP_SLACK_FORWARD_ENV:   "SLACK_BOT_TOKEN",
        SLACK_BOT_TOKEN:         process.env.SLACK_BOT_TOKEN!,
    },
});
```

- [ ] **Step 2: Wire OAuth into KK's Slack workspace**

This is a manual one-time step for the demo (single-tenant). KK:
1. Create a new Slack App at api.slack.com/apps with Bot Token Scopes `chat:write`, `users:read`, `users:read.email`.
2. Install to KK's workspace.
3. Copy the Bot User OAuth Token (`xoxb-...`) into `platform/.env` as `SLACK_BOT_TOKEN=xoxb-...`.

- [ ] **Step 3: Write the failing test**

```python
# platform/lambda/tools/tests/test_slack_dm.py
import pytest
from unittest.mock import patch, MagicMock
from tools.slack_dm import handle


@patch("tools.slack_dm._mcp_client")
def test_resolves_user_and_sends_dm(mock_client):
    mock_client.call.side_effect = [
        # First call: users.lookupByEmail
        {"user": {"id": "U123ABC"}},
        # Second call: chat.postMessage
        {"ts": "1717030000.001", "channel": "D123"},
    ]
    result = handle(
        {"user_lookup": "sarah.chen@acme.io", "message": "Heads up"},
        {"sub": "test-user"},
    )
    assert result["ts"] == "1717030000.001"
    assert result["channel"] == "D123"
    assert "speakable" in result
    assert "Sarah" in result["speakable"] or "Slack" in result["speakable"]


@patch("tools.slack_dm._mcp_client")
def test_user_not_found(mock_client):
    mock_client.call.return_value = {"error": "users_not_found"}
    result = handle(
        {"user_lookup": "ghost@acme.io", "message": "Hi"},
        {"sub": "test-user"},
    )
    assert result["sent"] is False
    assert result["reason"] == "user_not_found"
```

- [ ] **Step 4: Run test to verify it fails**

```bash
cd platform/lambda
python -m pytest tools/tests/test_slack_dm.py -v
```
Expected: FAIL — `NotImplementedError("Task 8")`.

- [ ] **Step 5: Implement `slack_dm.py`**

```python
# platform/lambda/tools/slack_dm.py
"""Send a Slack DM to a user via the Slack MCP server.

Two-step: look up user by email, then post message to their DM channel.
"""
from __future__ import annotations
from _shared.mcp_client import MCPClient, ToolRegistryEntry
from tools.main import register


_mcp_client = MCPClient()
_mcp_client.register("slack_lookup_user", ToolRegistryEntry(
    server="slack",
    tool="slack_get_user_by_email",  # check actual tool name in Anthropic reference server
    args_mapping=lambda args: {"email": args["email"]},
))
_mcp_client.register("slack_post_message", ToolRegistryEntry(
    server="slack",
    tool="slack_post_message",
    args_mapping=lambda args: {"channel": args["channel"], "text": args["text"]},
))


@register("slack_dm")
def handle(args: dict, claims: dict) -> dict:
    email   = args["user_lookup"]
    message = args["message"]

    lookup = _mcp_client.call("slack_lookup_user", {"email": email})
    user = lookup.get("user") or lookup.get("data", {}).get("user")
    if not user or "id" not in user:
        return {
            "sent":      False,
            "reason":    "user_not_found",
            "speakable": f"Could not find {email} in Slack.",
        }
    user_id = user["id"]
    post = _mcp_client.call("slack_post_message", {"channel": user_id, "text": message})
    return {
        "sent":      True,
        "ts":        post.get("ts"),
        "channel":   post.get("channel"),
        "speakable": f"Slack DM sent to {email.split('@')[0]}.",
    }
```

- [ ] **Step 6: Run test to verify it passes**

```bash
cd platform/lambda
python -m pytest tools/tests/test_slack_dm.py -v
```
Expected: PASS — 2 tests green.

- [ ] **Step 7: Build + deploy + live smoke test**

```bash
cd platform/lambda/tools
./build.sh
cd ../../
npx cdk deploy CisoCopilotApi --require-approval never
```

Live smoke:

```bash
aws lambda invoke --function-name <ToolsFn arn> \
  --payload '{"requestContext":{"authorizer":{"claims":{"sub":"x"}}},"pathParameters":{"tool_name":"slack_dm"},"body":"{\"user_lookup\":\"kkmookhey@gmail.com\",\"message\":\"test from Shasta\"}"}' \
  /tmp/out.json && cat /tmp/out.json
```
Expected: `{"sent": true, "ts": "...", "channel": "...", "speakable": "Slack DM sent to kkmookhey."}` AND a real DM lands in KK's Slack.

- [ ] **Step 8: Commit**

```bash
git add platform/lambda/tools/slack_dm.py \
        platform/lambda/tools/tests/test_slack_dm.py \
        platform/lambda/tools/Dockerfile \
        platform/lambda/tools/build.sh \
        platform/lib/api-stack.ts
git commit -m "feat(tools): slack_dm via Slack MCP server

Two-step: lookup user by email, then post message. Tools Lambda
converted to container image so it can shell out to npx for the
Anthropic-reference Slack MCP server. Smoke-tested against KK's
workspace end-to-end."
```

---

### Task 9: Atlassian MCP + `create_jira_ticket` tool

**Files:**
- Modify: `platform/lambda/tools/create_jira_ticket.py`
- Create: `platform/lambda/tools/tests/test_create_jira_ticket.py`

**Background:** Uses `mcp-atlassian` (pip-installable Python MCP server already in the Dockerfile from Task 8). Configure via env vars: `JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN`.

- [ ] **Step 1: Manual OAuth/token setup (KK)**

Generate an Atlassian API token at https://id.atlassian.com/manage-profile/security/api-tokens. Add to `platform/.env`:

```
JIRA_URL=https://transilience.atlassian.net
[email protected]
JIRA_API_TOKEN=<token>
```

- [ ] **Step 2: Write the failing test**

```python
# platform/lambda/tools/tests/test_create_jira_ticket.py
import pytest
from unittest.mock import patch
from tools.create_jira_ticket import handle


@patch("tools.create_jira_ticket._mcp_client")
def test_creates_ticket(mock_client):
    mock_client.call.return_value = {
        "key": "ITSEC-3091",
        "self": "https://transilience.atlassian.net/rest/api/2/issue/12345",
    }
    result = handle({
        "project_key":      "ITSEC",
        "summary":          "Provision Sarah on ChatGPT Enterprise",
        "description":      "Personal-tier ChatGPT use detected.",
        "assignee_lookup":  "priya@transilience.ai",
    }, {"sub": "test-user"})
    assert result["key"] == "ITSEC-3091"
    assert "url" in result
    assert "speakable" in result
    assert "ITSEC-3091" in result["speakable"]


def test_missing_required_arg():
    with pytest.raises(KeyError):
        handle({"summary": "missing project_key"}, {"sub": "x"})
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd platform/lambda
python -m pytest tools/tests/test_create_jira_ticket.py -v
```
Expected: FAIL — `NotImplementedError("Task 9")`.

- [ ] **Step 4: Implement `create_jira_ticket.py`**

```python
# platform/lambda/tools/create_jira_ticket.py
"""Create a JIRA issue via the mcp-atlassian MCP server."""
from __future__ import annotations
import os

from _shared.mcp_client import MCPClient, ToolRegistryEntry
from tools.main import register


_mcp_client = MCPClient()
_mcp_client.register("jira_create_issue", ToolRegistryEntry(
    server="atlassian",
    tool="jira_create_issue",
    args_mapping=lambda a: {
        "project_key":  a["project_key"],
        "summary":      a["summary"],
        "issue_type":   a.get("issue_type", "Task"),
        "description":  a.get("description", ""),
        "assignee":     a.get("assignee_lookup"),
    },
))


@register("create_jira_ticket")
def handle(args: dict, claims: dict) -> dict:
    project_key = args["project_key"]
    summary     = args["summary"]

    result = _mcp_client.call("jira_create_issue", args)
    key = result.get("key")
    if not key:
        return {
            "created":   False,
            "reason":    "no_key_returned",
            "raw":       result,
            "speakable": "JIRA returned no issue key — check the project key and assignee.",
        }
    base = os.environ.get("JIRA_URL", "").rstrip("/")
    return {
        "created":   True,
        "key":       key,
        "url":       f"{base}/browse/{key}" if base else key,
        "speakable": f"JIRA {key} opened, assigned to {args.get('assignee_lookup', 'unassigned').split('@')[0]}.",
    }
```

- [ ] **Step 5: Wire env vars + MCP transport in CDK**

In `api-stack.ts`, extend `toolsFn` environment:

```typescript
environment: {
    // ...existing
    MCP_ATLASSIAN_COMMAND:     "mcp-atlassian",
    MCP_ATLASSIAN_FORWARD_ENV: "JIRA_URL,JIRA_USERNAME,JIRA_API_TOKEN",
    JIRA_URL:                  process.env.JIRA_URL!,
    JIRA_USERNAME:             process.env.JIRA_USERNAME!,
    JIRA_API_TOKEN:            process.env.JIRA_API_TOKEN!,
},
```

- [ ] **Step 6: Run test + deploy + live smoke**

```bash
cd platform/lambda
python -m pytest tools/tests/test_create_jira_ticket.py -v
cd tools && ./build.sh && cd ../../
npx cdk deploy CisoCopilotApi --require-approval never
```

Live smoke (creates a real test ticket in KK's JIRA):

```bash
aws lambda invoke --function-name <ToolsFn arn> \
  --payload '{"requestContext":{"authorizer":{"claims":{"sub":"x"}}},"pathParameters":{"tool_name":"create_jira_ticket"},"body":"{\"project_key\":\"DEMO\",\"summary\":\"Shasta MCP test ticket\",\"description\":\"Created by Wow demo wiring test.\"}"}' \
  /tmp/out.json && cat /tmp/out.json
```

Expected: real JIRA issue created in DEMO project; response includes `{"created": true, "key": "DEMO-1", ...}`.

- [ ] **Step 7: Commit**

```bash
git add platform/lambda/tools/create_jira_ticket.py \
        platform/lambda/tools/tests/test_create_jira_ticket.py \
        platform/lib/api-stack.ts
git commit -m "feat(tools): create_jira_ticket via Atlassian MCP

Uses mcp-atlassian (Python). Args: project_key, summary, description,
assignee_lookup. Returns key + browse URL + speakable narration."
```

---

### Task 10: GitHub MCP + `create_pr_with_bump` tool

**Files:**
- Modify: `platform/lambda/tools/create_pr_with_bump.py`
- Create: `platform/lambda/tools/tests/test_create_pr_with_bump.py`

**Background:** Anthropic-reference `@modelcontextprotocol/server-github` (already installed in Task 8's Dockerfile). Needs a GitHub personal access token (PAT) with `repo` scope for KK's org. The existing GitHub App (`ai_github` Lambda) is read-only via App authentication; the PR-write path uses a PAT instead for the demo. Multi-tenant App-with-PR-write-scope is a follow-up.

- [ ] **Step 1: Manual setup (KK)**

Create a GitHub PAT at https://github.com/settings/tokens with `repo` scope. Add to `platform/.env`:

```
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...
```

- [ ] **Step 2: Write the failing test**

```python
# platform/lambda/tools/tests/test_create_pr_with_bump.py
import pytest
from unittest.mock import patch
from tools.create_pr_with_bump import handle, _bump_version_in_requirements


def test_bump_replaces_pinned_version():
    content = "fastapi==0.95.0\nlangchain==0.0.184\npydantic>=2.0\n"
    out = _bump_version_in_requirements(content, "langchain", "0.0.354")
    assert "langchain==0.0.354" in out
    assert "langchain==0.0.184" not in out
    # Other lines untouched.
    assert "fastapi==0.95.0" in out


def test_bump_no_match_returns_original():
    content = "fastapi==0.95.0\n"
    out = _bump_version_in_requirements(content, "langchain", "0.0.354")
    assert out == content


@patch("tools.create_pr_with_bump._mcp_client")
def test_create_pr_orchestration(mock_client):
    mock_client.call.side_effect = [
        # 1. get_file_contents -> current requirements.txt
        {"content": "langchain==0.0.184\n", "sha": "blob-sha"},
        # 2. create_branch
        {"ref": "refs/heads/shasta/bump-langchain-0.0.354"},
        # 3. create_or_update_file -> commit
        {"commit": {"sha": "commit-sha"}},
        # 4. create_pull_request
        {"number": 42, "html_url": "https://github.com/acme/paying-system/pull/42"},
    ]
    result = handle({
        "repo":             "acme/paying-system",
        "dependency":       "langchain",
        "target_version":   "0.0.354",
        "reviewer_lookup":  "priya",
        "manifest_path":    "requirements.txt",
    }, {"sub": "x"})
    assert result["pr_number"] == 42
    assert "url" in result
    assert "speakable" in result
    assert "PR" in result["speakable"]
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd platform/lambda
python -m pytest tools/tests/test_create_pr_with_bump.py -v
```
Expected: FAIL — `NotImplementedError("Task 10")`.

- [ ] **Step 4: Implement `create_pr_with_bump.py`**

```python
# platform/lambda/tools/create_pr_with_bump.py
"""Open a PR that bumps a single dependency pin in a manifest file."""
from __future__ import annotations
import base64
import re

from _shared.mcp_client import MCPClient, ToolRegistryEntry
from tools.main import register


_mcp_client = MCPClient()
_mcp_client.register("github_get_file", ToolRegistryEntry(
    server="github",
    tool="get_file_contents",
    args_mapping=lambda a: {"owner": a["owner"], "repo": a["repo"], "path": a["path"]},
))
_mcp_client.register("github_create_branch", ToolRegistryEntry(
    server="github",
    tool="create_branch",
    args_mapping=lambda a: {"owner": a["owner"], "repo": a["repo"],
                            "branch": a["branch"], "from_branch": a.get("from_branch", "main")},
))
_mcp_client.register("github_put_file", ToolRegistryEntry(
    server="github",
    tool="create_or_update_file",
    args_mapping=lambda a: {"owner": a["owner"], "repo": a["repo"], "path": a["path"],
                            "content": a["content"], "message": a["message"],
                            "branch": a["branch"], "sha": a.get("sha")},
))
_mcp_client.register("github_create_pr", ToolRegistryEntry(
    server="github",
    tool="create_pull_request",
    args_mapping=lambda a: {"owner": a["owner"], "repo": a["repo"],
                            "title": a["title"], "head": a["head"],
                            "base": a.get("base", "main"), "body": a.get("body", "")},
))


def _bump_version_in_requirements(content: str, pkg: str, new_version: str) -> str:
    """Replace `pkg==<old>` with `pkg==<new>`. Leaves >= or other comparators alone."""
    pattern = re.compile(rf"^({re.escape(pkg)})==[\w\.\-]+", re.MULTILINE)
    return pattern.sub(f"{pkg}=={new_version}", content)


@register("create_pr_with_bump")
def handle(args: dict, claims: dict) -> dict:
    repo_full      = args["repo"]                              # "owner/repo"
    dependency     = args["dependency"]
    target_version = args["target_version"]
    manifest_path  = args.get("manifest_path", "requirements.txt")
    reviewer       = args.get("reviewer_lookup")
    owner, repo    = repo_full.split("/", 1)

    branch = f"shasta/bump-{dependency}-{target_version}"
    title  = f"Bump {dependency} to {target_version}"
    body   = (f"Shasta opened this PR after KEV-listed CVE matched against "
              f"`{dependency}` in active runtime use.\n\n"
              f"Reviewer suggested: {reviewer or 'unassigned'}.")

    # 1. Read current manifest.
    cur = _mcp_client.call("github_get_file", {
        "owner": owner, "repo": repo, "path": manifest_path,
    })
    raw_content = cur.get("content", "")
    if cur.get("encoding") == "base64":
        raw_content = base64.b64decode(raw_content).decode()
    new_content = _bump_version_in_requirements(raw_content, dependency, target_version)
    if new_content == raw_content:
        return {
            "created":   False,
            "reason":    "no_pin_to_bump",
            "speakable": f"No pin for {dependency} found in {manifest_path}.",
        }

    # 2. Branch from main.
    _mcp_client.call("github_create_branch", {
        "owner": owner, "repo": repo, "branch": branch, "from_branch": "main",
    })
    # 3. Commit the bumped manifest.
    _mcp_client.call("github_put_file", {
        "owner": owner, "repo": repo, "path": manifest_path,
        "content": new_content, "message": title, "branch": branch,
        "sha": cur.get("sha"),
    })
    # 4. Open PR.
    pr = _mcp_client.call("github_create_pr", {
        "owner": owner, "repo": repo, "title": title,
        "head": branch, "base": "main", "body": body,
    })
    return {
        "created":   True,
        "pr_number": pr.get("number"),
        "url":       pr.get("html_url"),
        "speakable": f"PR opened — link is in your Slack.",
    }
```

- [ ] **Step 5: Wire env in CDK**

```typescript
environment: {
    // ...existing
    MCP_GITHUB_COMMAND:       "npx -y @modelcontextprotocol/server-github",
    MCP_GITHUB_FORWARD_ENV:   "GITHUB_PERSONAL_ACCESS_TOKEN",
    GITHUB_PERSONAL_ACCESS_TOKEN: process.env.GITHUB_PERSONAL_ACCESS_TOKEN!,
},
```

- [ ] **Step 6: Run test + deploy + live smoke**

```bash
cd platform/lambda
python -m pytest tools/tests/test_create_pr_with_bump.py -v
cd tools && ./build.sh && cd ../../
npx cdk deploy CisoCopilotApi --require-approval never
```

Live smoke (in a test repo KK owns — use a real `requirements.txt`):

```bash
aws lambda invoke --function-name <ToolsFn arn> \
  --payload '{"requestContext":{"authorizer":{"claims":{"sub":"x"}}},"pathParameters":{"tool_name":"create_pr_with_bump"},"body":"{\"repo\":\"kkmookhey/shasta-test-repo\",\"dependency\":\"requests\",\"target_version\":\"2.32.0\",\"manifest_path\":\"requirements.txt\"}"}' \
  /tmp/out.json && cat /tmp/out.json
```

Expected: real PR appears in the test repo; response carries `pr_number` + `url`.

- [ ] **Step 7: Commit**

```bash
git add platform/lambda/tools/create_pr_with_bump.py \
        platform/lambda/tools/tests/test_create_pr_with_bump.py \
        platform/lib/api-stack.ts
git commit -m "feat(tools): create_pr_with_bump via GitHub MCP

Orchestrates: read manifest -> bump pin -> branch from main -> commit -> open PR.
Single-tenant PAT for demo; multi-tenant App-with-write follows."
```

---

### Task 11: `tail_lambda_logs_for_pattern` tool

**Files:**
- Modify: `platform/lambda/tools/tail_lambda_logs.py`
- Create: `platform/lambda/tools/tests/test_tail_lambda_logs.py`

**Background:** CloudWatch Logs Insights. Run `start_query` → poll `get_query_results` until `Status == "Complete"`.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/tools/tests/test_tail_lambda_logs.py
from unittest.mock import patch, MagicMock
from tools.tail_lambda_logs import handle


@patch("tools.tail_lambda_logs._logs")
def test_returns_matches(mock_logs):
    mock_logs.start_query.return_value = {"queryId": "q-123"}
    mock_logs.get_query_results.return_value = {
        "status": "Complete",
        "results": [
            [{"field": "@timestamp", "value": "2026-05-27 12:00:00"},
             {"field": "@message",   "value": "EVENT: exec_payload received"}],
        ],
    }
    result = handle({
        "function_name":    "prod-ai-router",
        "regex":            "exec_payload",
        "window_hours":     72,
    }, {"sub": "x"})
    assert "matches" in result
    assert len(result["matches"]) == 1
    assert "speakable" in result


@patch("tools.tail_lambda_logs._logs")
def test_no_matches(mock_logs):
    mock_logs.start_query.return_value = {"queryId": "q-456"}
    mock_logs.get_query_results.return_value = {"status": "Complete", "results": []}
    result = handle({
        "function_name":    "prod-ai-router",
        "regex":            "exec_payload",
        "window_hours":     72,
    }, {"sub": "x"})
    assert result["matches"] == []
    assert "no matches" in result["speakable"].lower() or "nothing" in result["speakable"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd platform/lambda
python -m pytest tools/tests/test_tail_lambda_logs.py -v
```
Expected: FAIL — `NotImplementedError("Task 11")`.

- [ ] **Step 3: Implement `tail_lambda_logs.py`**

```python
# platform/lambda/tools/tail_lambda_logs.py
"""Search a Lambda's CloudWatch logs for a regex over a recent time window."""
from __future__ import annotations
import time
from datetime import datetime, timedelta, timezone

import boto3
from tools.main import register


_logs = boto3.client("logs")


@register("tail_lambda_logs_for_pattern")
def handle(args: dict, claims: dict) -> dict:
    fn_name = args["function_name"]
    regex   = args["regex"]
    hours   = int(args.get("window_hours", 72))

    end_ts   = int(datetime.now(timezone.utc).timestamp())
    start_ts = end_ts - (hours * 3600)
    log_group = f"/aws/lambda/{fn_name}"

    insights_query = (
        f"fields @timestamp, @message | "
        f"filter @message like /{regex}/ | "
        f"sort @timestamp desc | limit 100"
    )

    start = _logs.start_query(
        logGroupName=log_group,
        startTime=start_ts, endTime=end_ts,
        queryString=insights_query,
    )
    qid = start["queryId"]

    # Poll up to 30 seconds.
    for _ in range(30):
        time.sleep(1)
        rs = _logs.get_query_results(queryId=qid)
        if rs["status"] == "Complete":
            break
    else:
        return {
            "matches":   [],
            "reason":    "query_timeout",
            "speakable": f"Log query against {fn_name} timed out.",
        }

    matches = []
    for row in rs.get("results", []):
        d = {c["field"]: c["value"] for c in row}
        matches.append({"timestamp": d.get("@timestamp"), "message": d.get("@message", "")[:500]})

    if not matches:
        return {
            "matches":   [],
            "speakable": f"Nothing matching that pattern in {fn_name} over the last {hours} hours.",
        }
    return {
        "matches":   matches,
        "speakable": f"Found {len(matches)} matches in {fn_name} over the last {hours} hours.",
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd platform/lambda
python -m pytest tools/tests/test_tail_lambda_logs.py -v
```
Expected: PASS — 2 tests green.

- [ ] **Step 5: Grant CloudWatch Logs read in CDK**

In `api-stack.ts`, after `toolsFn` is created:

```typescript
toolsFn.addToRolePolicy(new iam.PolicyStatement({
    actions: ["logs:StartQuery", "logs:GetQueryResults"],
    resources: ["*"],
}));
```

- [ ] **Step 6: Deploy + commit**

```bash
cd platform
./lambda/tools/build.sh
npx cdk deploy CisoCopilotApi --require-approval never
git add platform/lambda/tools/tail_lambda_logs.py \
        platform/lambda/tools/tests/test_tail_lambda_logs.py \
        platform/lib/api-stack.ts
git commit -m "feat(tools): tail_lambda_logs_for_pattern via Logs Insights

Synchronous query with 30s poll cap. Returns matches list + speakable
summary suitable for Shasta to narrate."
```

---

### Task 12: `run_forensic_scan` stub

**Files:**
- Modify: `platform/lambda/tools/run_forensic_scan.py`
- Create: `platform/lambda/tools/tests/test_run_forensic_scan.py`

**Background:** For the demo, this returns a `scan_id` + ETA immediately. The actual "scan" is a 60-second timer that then fires an agent-initiated callback push (Task 17) with a staged "clean" result. Implementation is a tool that schedules an EventBridge one-time event.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/tools/tests/test_run_forensic_scan.py
from unittest.mock import patch
from tools.run_forensic_scan import handle


@patch("tools.run_forensic_scan._schedule_callback")
def test_returns_scan_id_and_eta(mock_schedule):
    result = handle({
        "target_arn":        "arn:aws:lambda:us-east-1:111:function:prod-ai-router",
        "check_kind":        "supply_chain_active_exploit",
        "conversation_id":   "conv-abc",
    }, {"sub": "x"})
    assert "scan_id" in result
    assert result["eta_seconds"] > 0
    assert "speakable" in result
    mock_schedule.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd platform/lambda
python -m pytest tools/tests/test_run_forensic_scan.py -v
```
Expected: FAIL — `NotImplementedError("Task 12")`.

- [ ] **Step 3: Implement `run_forensic_scan.py`**

```python
# platform/lambda/tools/run_forensic_scan.py
"""Staged forensic-scan tool for the recorded demo.

Returns a scan_id + ETA immediately, schedules a one-time EventBridge rule
to fire 60s later. That rule triggers the callback-push helper (Task 17)
which delivers the staged 'clean' result as an APNs push tied back to the
conversation_id.
"""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from tools.main import register


_events = boto3.client("events")
_CALLBACK_FN_ARN = os.environ.get("FORENSIC_CALLBACK_FN_ARN", "")
_ETA_SECONDS = 60


@register("run_forensic_scan")
def handle(args: dict, claims: dict) -> dict:
    target_arn       = args["target_arn"]
    check_kind       = args["check_kind"]
    conversation_id  = args["conversation_id"]
    scan_id          = f"scan-{uuid.uuid4().hex[:12]}"

    _schedule_callback(
        scan_id=scan_id, target_arn=target_arn, check_kind=check_kind,
        conversation_id=conversation_id,
    )

    return {
        "scan_id":      scan_id,
        "eta_seconds":  _ETA_SECONDS,
        "speakable":    f"Forensic scan started. I'll ping you when it's done — about a minute.",
    }


def _schedule_callback(*, scan_id: str, target_arn: str, check_kind: str,
                       conversation_id: str) -> None:
    if not _CALLBACK_FN_ARN:
        print("FORENSIC_CALLBACK_FN_ARN not set — skipping scheduling (test mode)")
        return
    fire_at = datetime.now(timezone.utc) + timedelta(seconds=_ETA_SECONDS)
    rule_name = f"forensic-{scan_id}"
    cron_expr = fire_at.strftime("cron(%M %H %d %m ? %Y)")
    _events.put_rule(
        Name=rule_name,
        ScheduleExpression=cron_expr,
        State="ENABLED",
    )
    _events.put_targets(
        Rule=rule_name,
        Targets=[{
            "Id":    "1",
            "Arn":   _CALLBACK_FN_ARN,
            "Input": json.dumps({
                "scan_id": scan_id, "target_arn": target_arn,
                "check_kind": check_kind, "conversation_id": conversation_id,
                "self_delete_rule": rule_name,
            }),
        }],
    )
```

- [ ] **Step 4: Run test + commit**

```bash
cd platform/lambda
python -m pytest tools/tests/test_run_forensic_scan.py -v
git add platform/lambda/tools/run_forensic_scan.py \
        platform/lambda/tools/tests/test_run_forensic_scan.py
git commit -m "feat(tools): run_forensic_scan stub for demo

Returns scan_id + 60s ETA immediately; schedules EventBridge one-time
rule to fire the callback Lambda (Task 17) with the staged 'clean'
result tied back to conversation_id."
```

---

### Task 13: CVE-vs-AI-inventory matcher Lambda

**Files:**
- Create: `platform/lambda/ai_supply_chain_matcher/main.py`
- Create: `platform/lambda/ai_supply_chain_matcher/build.sh`
- Create: `platform/lambda/ai_supply_chain_matcher/tests/test_matcher.py`
- Modify: `platform/lib/scan-stack.ts`

**Background:** Triggered by SQS message after the AI scanner finishes. Joins `findings.kind='sca_vuln'` (Trivy output) with `entities.kind='ai_framework'` (matched by package name) joined to `ai_agent` via `edges.kind='imports'`. Cross-references against `threat_indicators` where `source='kev'`. Emits a new finding `kind='ai_supply_chain_active'` at severity CRITICAL when both (KEV-listed AND actively imported).

- [ ] **Step 1: Scaffold directory + build script**

```bash
mkdir -p platform/lambda/ai_supply_chain_matcher/tests
touch platform/lambda/ai_supply_chain_matcher/__init__.py \
      platform/lambda/ai_supply_chain_matcher/tests/__init__.py
```

`platform/lambda/ai_supply_chain_matcher/build.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BUILD=$HERE/build
ZIP=$HERE/matcher.zip
rm -rf "$BUILD" "$ZIP"
mkdir -p "$BUILD"
cp "$HERE"/*.py "$BUILD"
cp -r "$HERE/.."/_shared "$BUILD/_shared"
(cd "$BUILD" && zip -rq "$ZIP" .)
echo "built: $ZIP"
```
```bash
chmod +x platform/lambda/ai_supply_chain_matcher/build.sh
```

- [ ] **Step 2: Write the failing test**

```python
# platform/lambda/ai_supply_chain_matcher/tests/test_matcher.py
import json
from unittest.mock import patch, MagicMock
from ai_supply_chain_matcher.main import handler, _find_matches


@patch("ai_supply_chain_matcher.main._rds")
def test_find_matches_returns_kev_listed_and_actively_imported(mock_rds):
    # Mock the Aurora query: one row meeting both conditions.
    mock_rds.execute_statement.return_value = {
        "records": [[
            {"stringValue": "f-trivy-1"},                    # trivy_finding_id
            {"stringValue": "langchain"},                    # package
            {"stringValue": "0.0.184"},                      # version
            {"stringValue": "CVE-2026-0470"},                # cve
            {"stringValue": "lc-entity-id"},                 # framework_entity_id
            {"stringValue": "agent-id"},                     # agent_entity_id
            {"stringValue": "pricing-agent"},                # agent_name
            {"stringValue": "acme/paying-system"},           # repo_full_name
        ]]
    }
    matches = _find_matches(tenant_id="t-1")
    assert len(matches) == 1
    assert matches[0]["package"] == "langchain"
    assert matches[0]["cve"] == "CVE-2026-0470"
    assert matches[0]["agent_name"] == "pricing-agent"


@patch("ai_supply_chain_matcher.main._emit_finding")
@patch("ai_supply_chain_matcher.main._fire_push")
@patch("ai_supply_chain_matcher.main._find_matches")
def test_handler_emits_finding_per_match(mock_find, mock_push, mock_emit):
    mock_find.return_value = [{
        "package": "langchain", "version": "0.0.184",
        "cve": "CVE-2026-0470", "agent_name": "pricing-agent",
        "agent_entity_id": "agent-id", "framework_entity_id": "lc-entity-id",
        "repo_full_name": "acme/paying-system", "trivy_finding_id": "f-1",
    }]
    event = {"Records": [{"body": json.dumps({"tenant_id": "t-1", "scan_id": "s-1"})}]}
    handler(event, None)
    mock_emit.assert_called_once()
    mock_push.assert_called_once()
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd platform/lambda
python -m pytest ai_supply_chain_matcher/tests/test_matcher.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement the matcher**

```python
# platform/lambda/ai_supply_chain_matcher/main.py
"""CVE-vs-AI-inventory matcher.

Triggered by SQS after the AI scanner emits sca_vuln findings. Joins them
with the ai_framework -> ai_agent edge graph + the KEV threat_indicators
table. When both conditions hold (KEV-listed AND actively imported), emits
a new ai_supply_chain_active finding at CRITICAL severity and fires a push.
"""
from __future__ import annotations
import json
import os
import uuid
from typing import Any

import boto3

from _shared import push as push_mod


DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
APNS_PLATFORM_APP_ARN = os.environ.get("APNS_PLATFORM_APP_ARN", "")

_rds = boto3.client("rds-data")


_MATCH_SQL = """
SELECT
  trivy.id::text                                        AS trivy_finding_id,
  trivy.evidence_packet->>'package'                     AS package,
  trivy.evidence_packet->>'version'                     AS version,
  trivy.evidence_packet->>'cve'                         AS cve,
  framework.id::text                                    AS framework_entity_id,
  agent.id::text                                        AS agent_entity_id,
  agent.display_name                                    AS agent_name,
  repo.display_name                                     AS repo_full_name
FROM findings trivy
JOIN entities framework
  ON framework.tenant_id = trivy.tenant_id
  AND framework.kind = 'ai_framework'
  AND LOWER(framework.display_name) = LOWER(trivy.evidence_packet->>'package')
JOIN edges e
  ON e.tenant_id = trivy.tenant_id
  AND e.target_id = framework.id
  AND e.kind = 'imports'
JOIN entities agent
  ON agent.id = e.source_id
  AND agent.kind = 'ai_agent'
LEFT JOIN entities repo
  ON repo.id = agent.parent_id
  AND repo.kind = 'github_repo'
JOIN threat_indicators kev
  ON kev.kind = 'cve'
  AND kev.source = 'kev'
  AND kev.value = trivy.evidence_packet->>'cve'
WHERE trivy.tenant_id = CAST(:t AS UUID)
  AND trivy.kind = 'sca_vuln'
  AND trivy.severity IN ('critical', 'high')
  AND NOT EXISTS (
    -- skip if we've already emitted a match for this triple
    SELECT 1 FROM findings prior
    WHERE prior.tenant_id = trivy.tenant_id
      AND prior.kind = 'ai_supply_chain_active'
      AND prior.evidence_packet->>'package' = trivy.evidence_packet->>'package'
      AND prior.evidence_packet->>'cve' = trivy.evidence_packet->>'cve'
      AND prior.evidence_packet->>'agent_entity_id' = agent.id::text
  )
"""


def handler(event: dict, context) -> None:
    for record in event.get("Records", []):
        try:
            body = json.loads(record["body"])
            tenant_id = body["tenant_id"]
            scan_id   = body.get("scan_id")
            matches = _find_matches(tenant_id=tenant_id)
            print(f"matcher: tenant={tenant_id} scan={scan_id} matches={len(matches)}")
            for m in matches:
                finding_id = _emit_finding(tenant_id=tenant_id, scan_id=scan_id, match=m)
                _fire_push(tenant_id=tenant_id, finding_id=finding_id, match=m)
        except Exception as e:
            print(f"matcher error: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            # Let SQS retry per its visibility timeout.
            raise


def _find_matches(*, tenant_id: str) -> list[dict[str, Any]]:
    rs = _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=_MATCH_SQL,
        parameters=[{"name": "t", "value": {"stringValue": tenant_id}}],
    )
    out = []
    for row in rs.get("records", []):
        out.append({
            "trivy_finding_id":    _str(row, 0),
            "package":             _str(row, 1),
            "version":             _str(row, 2),
            "cve":                 _str(row, 3),
            "framework_entity_id": _str(row, 4),
            "agent_entity_id":     _str(row, 5),
            "agent_name":          _str(row, 6),
            "repo_full_name":      _str(row, 7),
        })
    return out


def _str(row: list, i: int) -> str:
    return row[i].get("stringValue", "") if i < len(row) else ""


def _emit_finding(*, tenant_id: str, scan_id: str | None, match: dict) -> str:
    finding_id = str(uuid.uuid4())
    evidence = json.dumps({
        "package":             match["package"],
        "version":             match["version"],
        "cve":                 match["cve"],
        "agent_name":          match["agent_name"],
        "agent_entity_id":     match["agent_entity_id"],
        "framework_entity_id": match["framework_entity_id"],
        "repo_full_name":      match["repo_full_name"],
        "kev_listed":          True,
        "actively_imported":   True,
    })
    title = (f"{match['package']} {match['version']} ({match['cve']}) actively "
             f"imported by {match['agent_name']}")
    _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql=(
            "INSERT INTO findings (id, tenant_id, scan_id, kind, severity, title, "
            "evidence_packet, frameworks, status) "
            "VALUES (CAST(:id AS UUID), CAST(:t AS UUID), CAST(:s AS UUID), "
            "        'ai_supply_chain_active', 'critical', :title, "
            "        CAST(:ep AS JSONB), CAST('[]' AS JSONB), 'open')"
        ),
        parameters=[
            {"name": "id",    "value": {"stringValue": finding_id}},
            {"name": "t",     "value": {"stringValue": tenant_id}},
            {"name": "s",     "value": {"stringValue": scan_id or "00000000-0000-0000-0000-000000000000"}},
            {"name": "title", "value": {"stringValue": title[:500]}},
            {"name": "ep",    "value": {"stringValue": evidence}},
        ],
    )
    return finding_id


def _fire_push(*, tenant_id: str, finding_id: str, match: dict) -> None:
    if not APNS_PLATFORM_APP_ARN:
        print("APNS_PLATFORM_APP_ARN not set — skipping push")
        return
    tokens = push_mod.tokens_for_tenant(tenant_id, rds=_rds,
                                        db_cluster_arn=DB_CLUSTER_ARN,
                                        db_secret_arn=DB_SECRET_ARN,
                                        db_name=DB_NAME)
    body = (f"AI Supply Chain · Critical — KEV CVE in your live "
            f"{match['agent_name']} ({match['package']})")
    push_mod.send_push_with_payload(
        device_tokens=tokens,
        platform_app_arn=APNS_PLATFORM_APP_ARN,
        body=body,
        payload={
            "finding_id":           finding_id,
            "kind_label":           "AI Supply Chain",
            "speakable_summary":    body,
        },
    )
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd platform/lambda
python -m pytest ai_supply_chain_matcher/tests/test_matcher.py -v
```
Expected: PASS — 2 tests green. (`tokens_for_tenant` and `send_push_with_payload` come from `_shared/push.py` in Task 14; this test stubs them via mocking the whole `_fire_push` call.)

- [ ] **Step 6: Register Lambda + SQS in CDK**

In `platform/lib/scan-stack.ts`, add:

```typescript
const matcherQueue = new sqs.Queue(this, "AiSupplyChainMatcherQueue", {
    visibilityTimeout: cdk.Duration.seconds(60),
});

const matcherFn = new lambda.Function(this, "AiSupplyChainMatcherFn", {
    runtime:    lambda.Runtime.PYTHON_3_12,
    handler:    "main.handler",
    code:       lambda.Code.fromAsset(path.join(__dirname, "../lambda/ai_supply_chain_matcher"), {
        bundling: { /* match existing patterns */ },
    }),
    timeout:    cdk.Duration.seconds(30),
    memorySize: 512,
    environment: {
        DB_CLUSTER_ARN:        props.db.clusterArn,
        DB_SECRET_ARN:         props.db.secret.secretArn,
        DB_NAME:               props.db.dbName,
        APNS_PLATFORM_APP_ARN: process.env.APNS_PLATFORM_APP_ARN!,
    },
});
matcherFn.addEventSource(new lambdaEvents.SqsEventSource(matcherQueue));
props.db.grantDataApiAccess(matcherFn);
matcherFn.addToRolePolicy(new iam.PolicyStatement({
    actions: ["sns:CreatePlatformEndpoint", "sns:Publish"],
    resources: ["*"],
}));
```

Grant the AI scanner permission to send to this queue:

```typescript
matcherQueue.grantSendMessages(aiScannerRole);
```

And export the queue URL so the scanner Lambda can use it:

```typescript
aiScannerFn.addEnvironment("AI_SUPPLY_CHAIN_MATCHER_QUEUE_URL", matcherQueue.queueUrl);
```

- [ ] **Step 7: Send to matcher queue at end of AI scanner**

In `platform/lambda/ai_scanner/main.py` (or wherever the scan completion handler is), after `unified_writer.commit_scan(...)`:

```python
import os, json
import boto3

_sqs = boto3.client("sqs")
_MATCHER_Q = os.environ.get("AI_SUPPLY_CHAIN_MATCHER_QUEUE_URL")

if _MATCHER_Q:
    _sqs.send_message(
        QueueUrl=_MATCHER_Q,
        MessageBody=json.dumps({"tenant_id": tenant_id, "scan_id": scan_id}),
    )
    print(f"matcher: enqueued tenant={tenant_id} scan={scan_id}")
```

- [ ] **Step 8: Build + deploy**

```bash
./platform/lambda/ai_supply_chain_matcher/build.sh
cd platform
npx cdk deploy CisoCopilotScan --require-approval never
```
Expected: matcher Lambda + queue created; AI scanner gets new env var.

- [ ] **Step 9: Commit**

```bash
git add platform/lambda/ai_supply_chain_matcher/ platform/lib/scan-stack.ts \
        platform/lambda/ai_scanner/main.py
git commit -m "feat(matcher): ai_supply_chain_active matcher Lambda

Joins sca_vuln + ai_framework->ai_agent edges + KEV. Emits
ai_supply_chain_active at CRITICAL with full evidence_packet; fires
push via _shared/push. Triggered from SQS after the AI scanner
completes."
```

---

## Phase 3 — Push triggers (Days 3–4)

### Task 14: Lift `push.py` to `_shared/`

**Files:**
- Create: `platform/lambda/_shared/push.py` (lift from `event_router/push.py`)
- Modify: `platform/lambda/event_router/push.py` (re-export from _shared) OR `event_router/main.py` (update import)
- Create: `platform/lambda/_shared/tests/test_push.py`

- [ ] **Step 1: Move push.py + add the new functions matcher and forensic need**

Read the existing `event_router/push.py` (already shown in plan context). Move its content into `_shared/push.py`, AND add two new functions:

```python
# platform/lambda/_shared/push.py
"""Push rule evaluation + SNS Mobile Push call. Shared across event_router,
the AI supply chain matcher, the Entra runner (personal-tier triggers), and
the forensic-scan callback Lambda."""
from __future__ import annotations
import json
import boto3


sns = boto3.client("sns")

PUSH_THRESHOLD       = "high"
PUSH_RATE_LIMIT_HOUR = 10

_SEV_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def should_push(severity: str, current_hour_count: int) -> bool:
    if severity == "critical":
        return True
    if _SEV_ORDER.get(severity, 0) < _SEV_ORDER[PUSH_THRESHOLD]:
        return False
    return current_hour_count < PUSH_RATE_LIMIT_HOUR


def format_push_body(*, kind: str, severity: str, title: str,
                     resource_arn: str | None, actor: str | None) -> str:
    bits = [kind, severity]
    rid  = (resource_arn or "").split("/")[-1] or (resource_arn or "")
    if rid: bits.append(rid)
    bits.append(title)
    if actor:
        parts = actor.split("/")
        bits.append(f"by {'/'.join(parts[-2:]) if len(parts) >= 2 else parts[-1]}")
    return " · ".join(bits)


def send_push(*, device_tokens: list[str], platform_app_arn: str, body: str) -> None:
    """Body-only push. Legacy callers use this."""
    send_push_with_payload(device_tokens=device_tokens,
                           platform_app_arn=platform_app_arn,
                           body=body, payload={})


def send_push_with_payload(*, device_tokens: list[str], platform_app_arn: str,
                            body: str, payload: dict) -> None:
    """Push with custom user-info payload — needed for the iOS app to deep-link
    into BriefingView with finding_id + speakable_summary."""
    aps_payload = {
        "aps": {"alert": body, "sound": "default"},
        **payload,
    }
    for token in device_tokens:
        ep = sns.create_platform_endpoint(
            PlatformApplicationArn=platform_app_arn,
            Token=token,
        )
        sns.publish(
            TargetArn=ep["EndpointArn"],
            Message=json.dumps({"APNS_SANDBOX": json.dumps(aps_payload)}),
            MessageStructure="json",
        )


def tokens_for_tenant(tenant_id: str, *, rds, db_cluster_arn: str,
                       db_secret_arn: str, db_name: str) -> list[str]:
    """Look up APNs device tokens for all users in a tenant. Returns []
    when none are registered (graceful no-op for push)."""
    rs = rds.execute_statement(
        resourceArn=db_cluster_arn, secretArn=db_secret_arn, database=db_name,
        sql=("SELECT device_token FROM users WHERE tenant_id = CAST(:t AS UUID) "
             "AND device_token IS NOT NULL"),
        parameters=[{"name": "t", "value": {"stringValue": tenant_id}}],
    )
    return [r[0].get("stringValue", "") for r in rs.get("records", []) if r[0].get("stringValue")]


def notify_tool_completion(*, tenant_id: str, conversation_id: str, body: str,
                            payload: dict, rds, db_cluster_arn: str,
                            db_secret_arn: str, db_name: str,
                            platform_app_arn: str) -> None:
    """Used by forensic-scan callback (and any other agent-initiated tool
    that takes long enough to background)."""
    tokens = tokens_for_tenant(tenant_id, rds=rds,
                                db_cluster_arn=db_cluster_arn,
                                db_secret_arn=db_secret_arn,
                                db_name=db_name)
    full_payload = {"conversation_id": conversation_id, **payload}
    send_push_with_payload(device_tokens=tokens,
                            platform_app_arn=platform_app_arn,
                            body=body, payload=full_payload)
```

- [ ] **Step 2: Make `event_router/push.py` a re-export**

Replace the existing `event_router/push.py` content with:

```python
# Re-export from _shared/push.py so legacy callers keep working.
from _shared.push import (
    should_push, format_push_body, send_push, send_push_with_payload,
    tokens_for_tenant, notify_tool_completion,
    PUSH_THRESHOLD, PUSH_RATE_LIMIT_HOUR,
)
```

And ensure `event_router/build.sh` (if it exists) vendors `_shared/` like the other Lambdas do.

- [ ] **Step 3: Write tests**

```python
# platform/lambda/_shared/tests/test_push.py
from unittest.mock import patch, MagicMock
from _shared.push import (
    should_push, format_push_body, send_push_with_payload,
    notify_tool_completion,
)


def test_critical_always_pushes():
    assert should_push("critical", current_hour_count=99) is True


def test_below_threshold_blocks():
    assert should_push("medium", current_hour_count=0) is False


def test_high_within_limit_allows():
    assert should_push("high", current_hour_count=5) is True


def test_high_over_limit_blocks():
    assert should_push("high", current_hour_count=10) is False


def test_format_push_body_basic():
    body = format_push_body(kind="drift", severity="high",
                            title="bucket policy changed",
                            resource_arn="arn:aws:s3:::my-bucket",
                            actor="arn:aws:iam::1:user/mike")
    assert "drift" in body and "high" in body and "my-bucket" in body
    assert "user/mike" in body


@patch("_shared.push.sns")
def test_send_push_with_payload_includes_extra(mock_sns):
    mock_sns.create_platform_endpoint.return_value = {"EndpointArn": "arn:..."}
    send_push_with_payload(
        device_tokens=["t1"], platform_app_arn="app-arn",
        body="Test", payload={"finding_id": "f-1"},
    )
    assert mock_sns.publish.called
    msg = mock_sns.publish.call_args.kwargs["Message"]
    import json
    inner = json.loads(json.loads(msg)["APNS_SANDBOX"])
    assert inner["finding_id"] == "f-1"
    assert inner["aps"]["alert"] == "Test"
```

- [ ] **Step 4: Run tests + ensure event_router still works**

```bash
cd platform/lambda
python -m pytest _shared/tests/test_push.py event_router/tests/ -v
```
Expected: PASS — all tests green (push tests + existing event_router tests).

- [ ] **Step 5: Build + deploy event_router (since its source changed)**

```bash
cd platform
./lambda/event_router/build.sh 2>/dev/null || true   # if it has its own build
npx cdk deploy CisoCopilotEvents --require-approval never --hotswap
```

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/_shared/push.py platform/lambda/_shared/tests/test_push.py \
        platform/lambda/event_router/push.py
git commit -m "refactor(_shared): lift push.py with new variants

Move from event_router to _shared. Add send_push_with_payload (so iOS
can deep-link via APNs userInfo), tokens_for_tenant, and
notify_tool_completion for agent-initiated callbacks."
```

---

### Task 15: Push trigger on new `ai_signin_personal_tier` finding

**Files:**
- Modify: `platform/lambda/shasta_runner_entra/main.py`

- [ ] **Step 1: Locate the existing personal-tier finding insert path**

```bash
grep -n "ai_signin_personal_tier\|_insert_finding_param_lists\|_FINDING_INSERT_SQL" \
  platform/lambda/shasta_runner_entra/main.py
```

Identify where personal-tier findings are committed.

- [ ] **Step 2: Add the push fire after commit**

Near the end of the handler in `shasta_runner_entra/main.py`, after findings are committed and the connection's `premium_required` flag is updated, add:

```python
# === Push for new personal-tier AI sign-in findings (recorded-demo Track A) ===
from _shared import push as push_mod

_APNS_PLATFORM_APP_ARN = os.environ.get("APNS_PLATFORM_APP_ARN", "")

def _fire_personal_tier_pushes(tenant_id: str, conn_id: str, scan_id: str) -> None:
    """For each newly-emitted personal-tier finding from THIS scan, fire one push."""
    if not _APNS_PLATFORM_APP_ARN:
        return
    rs = _rds.execute_statement(
        resourceArn=_DB_CLUSTER_ARN, secretArn=_DB_SECRET_ARN, database=_DB_NAME,
        sql=("SELECT id::text, title, evidence_packet->>'entra_upn' "
             "FROM findings "
             "WHERE tenant_id = CAST(:t AS UUID) AND scan_id = CAST(:s AS UUID) "
             "  AND kind = 'ai_signin_personal_tier'"),
        parameters=[
            {"name": "t", "value": {"stringValue": tenant_id}},
            {"name": "s", "value": {"stringValue": scan_id}},
        ],
    )
    tokens = push_mod.tokens_for_tenant(tenant_id, rds=_rds,
                                         db_cluster_arn=_DB_CLUSTER_ARN,
                                         db_secret_arn=_DB_SECRET_ARN,
                                         db_name=_DB_NAME)
    if not tokens:
        print(f"personal-tier push: tenant={tenant_id} no device tokens")
        return
    for row in rs.get("records", []):
        finding_id = row[0].get("stringValue")
        title      = row[1].get("stringValue", "")
        upn        = row[2].get("stringValue", "")
        speakable_summary = f"Shadow AI — {upn} using personal-tier AI app"
        push_mod.send_push_with_payload(
            device_tokens=tokens,
            platform_app_arn=_APNS_PLATFORM_APP_ARN,
            body=speakable_summary,
            payload={
                "finding_id":        finding_id,
                "kind_label":        "Shadow AI",
                "speakable_summary": speakable_summary,
            },
        )
        print(f"personal-tier push: finding={finding_id} fired")
```

Call `_fire_personal_tier_pushes(tenant_id, conn_id, scan_id)` after the commit step. Wrap in try/except so push failure doesn't fail the scan.

- [ ] **Step 3: Add APNS_PLATFORM_APP_ARN to the Entra runner Lambda env**

In `platform/lib/scan-stack.ts` (or wherever the entra runner is defined):

```typescript
entraRunnerFn.addEnvironment("APNS_PLATFORM_APP_ARN", process.env.APNS_PLATFORM_APP_ARN!);
entraRunnerFn.addToRolePolicy(new iam.PolicyStatement({
    actions: ["sns:CreatePlatformEndpoint", "sns:Publish"],
    resources: ["*"],
}));
```

- [ ] **Step 4: Build the scanner image with new code + push to ECR**

```bash
cd platform/lambda/shasta_runner_entra
./build.sh
```

- [ ] **Step 5: Deploy**

```bash
cd platform
npx cdk deploy CisoCopilotScan --require-approval never
```

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/shasta_runner_entra/main.py platform/lib/scan-stack.ts
git commit -m "feat(entra): push on new ai_signin_personal_tier findings

After every Entra scan, fires APNs push for each new personal-tier
finding with the speakable summary + finding_id payload. iOS app
launches into BriefingView on tap (Task 5 + Task 18)."
```

---

### Task 16: Push on `ai_supply_chain_active` is already in the matcher (Task 13)

This is covered by `_fire_push` in `ai_supply_chain_matcher/main.py`. No new code needed — the matcher's push fire is part of Task 13's commit.

**Verify with:**

```bash
grep -n "_fire_push\|send_push_with_payload" platform/lambda/ai_supply_chain_matcher/main.py
```

Expected: both functions referenced.

---

### Task 17: Agent-initiated callback push (forensic-scan completion)

**Files:**
- Create: `platform/lambda/forensic_callback/main.py`
- Create: `platform/lambda/forensic_callback/build.sh`
- Modify: `platform/lib/api-stack.ts` (or wherever `toolsFn` is) — to set `FORENSIC_CALLBACK_FN_ARN`

- [ ] **Step 1: Scaffold + implement the callback Lambda**

```bash
mkdir -p platform/lambda/forensic_callback
touch platform/lambda/forensic_callback/__init__.py
```

`platform/lambda/forensic_callback/main.py`:
```python
"""Triggered by the one-time EventBridge rule the run_forensic_scan tool
scheduled. Fires the 'I'll ping you when done' push with the staged
'clean' result tied to the conversation_id."""
from __future__ import annotations
import json
import os

import boto3
from _shared import push as push_mod


DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
APNS_PLATFORM_APP_ARN = os.environ["APNS_PLATFORM_APP_ARN"]

_rds    = boto3.client("rds-data")
_events = boto3.client("events")


def handler(event: dict, context) -> dict:
    scan_id         = event["scan_id"]
    target_arn      = event["target_arn"]
    conversation_id = event["conversation_id"]
    self_delete     = event.get("self_delete_rule")

    # Look up tenant from the conversation.
    rs = _rds.execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=DB_SECRET_ARN, database=DB_NAME,
        sql="SELECT tenant_id::text FROM conversations WHERE id = CAST(:c AS UUID) LIMIT 1",
        parameters=[{"name": "c", "value": {"stringValue": conversation_id}}],
    )
    rows = rs.get("records", [])
    if not rows:
        print(f"forensic_callback: no conversation {conversation_id}")
        return {"ok": False}
    tenant_id = rows[0][0].get("stringValue")

    # For the demo, the result is staged 'clean'.
    body = "Forensic scan complete — no anomalous activity detected."
    push_mod.notify_tool_completion(
        tenant_id=tenant_id, conversation_id=conversation_id,
        body=body,
        payload={
            "scan_id":           scan_id,
            "target_arn":        target_arn,
            "speakable_summary": body,
            "result":            "clean",
            "tool_name":         "run_forensic_scan",
        },
        rds=_rds, db_cluster_arn=DB_CLUSTER_ARN, db_secret_arn=DB_SECRET_ARN,
        db_name=DB_NAME, platform_app_arn=APNS_PLATFORM_APP_ARN,
    )

    # Clean up the one-time EventBridge rule.
    if self_delete:
        try:
            _events.remove_targets(Rule=self_delete, Ids=["1"])
            _events.delete_rule(Name=self_delete)
        except Exception as e:
            print(f"rule cleanup failed for {self_delete}: {e}")
    return {"ok": True}
```

`platform/lambda/forensic_callback/build.sh` (same pattern as ai_supply_chain_matcher).

- [ ] **Step 2: Register in CDK**

In `platform/lib/api-stack.ts` (or a new stack for misc callbacks):

```typescript
const forensicCallbackFn = new lambda.Function(this, "ForensicCallbackFn", {
    runtime:    lambda.Runtime.PYTHON_3_12,
    handler:    "main.handler",
    code:       lambda.Code.fromAsset(path.join(__dirname, "../lambda/forensic_callback")),
    timeout:    cdk.Duration.seconds(15),
    environment: {
        DB_CLUSTER_ARN:        props.db.clusterArn,
        DB_SECRET_ARN:         props.db.secret.secretArn,
        DB_NAME:               props.db.dbName,
        APNS_PLATFORM_APP_ARN: process.env.APNS_PLATFORM_APP_ARN!,
    },
});
props.db.grantDataApiAccess(forensicCallbackFn);
forensicCallbackFn.grantInvoke(new iam.ServicePrincipal("events.amazonaws.com"));
forensicCallbackFn.addToRolePolicy(new iam.PolicyStatement({
    actions: ["sns:CreatePlatformEndpoint", "sns:Publish"],
    resources: ["*"],
}));

// Wire the ARN into the tools Lambda so run_forensic_scan can schedule against it.
toolsFn.addEnvironment("FORENSIC_CALLBACK_FN_ARN", forensicCallbackFn.functionArn);
toolsFn.addToRolePolicy(new iam.PolicyStatement({
    actions: ["events:PutRule", "events:PutTargets", "events:RemoveTargets", "events:DeleteRule"],
    resources: ["*"],
}));
```

- [ ] **Step 3: Deploy + commit**

```bash
cd platform
./lambda/forensic_callback/build.sh
npx cdk deploy CisoCopilotApi --require-approval never
git add platform/lambda/forensic_callback/ platform/lib/api-stack.ts
git commit -m "feat(forensic-callback): agent-initiated push on tool completion

EventBridge rule scheduled by run_forensic_scan fires this Lambda
60s later. It looks up tenant from conversation_id, sends the 'clean'
result as APNs push with conversation_id in payload so iOS can rejoin
the same Realtime session and continue the briefing."
```

---

## Phase 4 — iOS auto-voice (Day 4)

### Task 18: BriefingView auto-mounts voice with incident context

**Files:**
- Modify: `ios/CISOCopilot/Views/BriefingView.swift`
- Modify: `ios/CISOCopilot/Services/VoiceClient.swift` (extend with `connect(seedDeveloperMessage:)`)

**Background:** When BriefingView appears, mount voice with a 300ms delay, send a developer message carrying the incident context (spec §7.4), Shasta speaks first.

- [ ] **Step 1: Read the existing VoiceClient**

```bash
grep -rn "class VoiceClient\|func connect\|RTCPeerConnection\|RTCDataChannel" \
  ios/CISOCopilot/Services/
```

Identify how the data channel is currently opened and how messages are sent. The seed message goes through the same data channel as tool-call dispatch.

- [ ] **Step 2: Add `connect(seedDeveloperMessage:)` variant to VoiceClient**

In `VoiceClient.swift`, add (preserving the existing `connect()` API):

```swift
extension VoiceClient {
    /// Connect to Realtime and, after the session opens, send a developer
    /// message containing the incident context so Shasta speaks first.
    func connect(seedDeveloperMessage: String) {
        self.pendingSeedMessage = seedDeveloperMessage
        self.connect()  // existing connect
    }
}

// Add to the VoiceClient class:
private var pendingSeedMessage: String?

private func sendSeedIfPending() {
    guard let seed = pendingSeedMessage, dataChannel?.readyState == .open else { return }
    // Realtime "developer message" — system role won't trigger an assistant
    // turn; "user" role with role-tag prefix is the pragmatic shape.
    let payload: [String: Any] = [
        "type": "conversation.item.create",
        "item": [
            "type":    "message",
            "role":    "user",
            "content": [["type": "input_text", "text": seed]],
        ],
    ]
    sendDataChannelJSON(payload)
    // Trigger response immediately.
    sendDataChannelJSON(["type": "response.create"])
    pendingSeedMessage = nil
}
```

In the existing data-channel `didChangeState` callback (or wherever the channel opens), call `sendSeedIfPending()`.

- [ ] **Step 3: Update BriefingView to auto-mount voice**

Replace the body of `BriefingView` (created in Task 5) with:

```swift
struct BriefingView: View {
    let incident: IncidentContext
    @EnvironmentObject var router: IncidentRouter
    @State private var voiceClient: VoiceClient?
    @State private var voiceState: VoiceState = .off

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: incident.payload["kind_label"] as? String == "AI Supply Chain"
                                  ? "shield.lefthalf.filled.trianglebadge.exclamationmark"
                                  : "exclamationmark.shield.fill")
                    .font(.system(size: 28))
                    .foregroundColor(.orange)
                VStack(alignment: .leading) {
                    Text(payloadString("kind_label", default: "Incident"))
                        .font(.headline)
                    Text(payloadString("speakable_summary", default: incident.findingId))
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }
            }
            .padding()
            .background(Color(.systemGray6))
            .cornerRadius(12)

            HStack(spacing: 8) {
                Circle()
                    .fill(voiceStateColor)
                    .frame(width: 10, height: 10)
                Text(voiceStateLabel)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Spacer()
        }
        .padding()
        .navigationTitle("Briefing")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Button("Done") {
                    voiceClient?.disconnect()
                    router.clear()
                }
            }
        }
        .onAppear {
            // 300ms delay so the screen renders before voice connects.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                let client = VoiceClient()
                client.onStateChange = { state in
                    DispatchQueue.main.async { self.voiceState = state }
                }
                client.connect(seedDeveloperMessage: buildSeedMessage())
                self.voiceClient = client
            }
        }
        .onDisappear {
            voiceClient?.disconnect()
        }
    }

    private func buildSeedMessage() -> String {
        // Convert the payload dict into the developer-message shape from spec §7.4.
        var lines = ["INCIDENT CONTEXT (the user just opened the app from a push notification):"]
        for key in ["finding_id", "kind_label", "speakable_summary"] {
            if let v = incident.payload[key] {
                lines.append("  \(key): \(v)")
            }
        }
        // Any other payload fields, dumped raw.
        for (k, v) in incident.payload {
            if !["finding_id", "kind_label", "speakable_summary"].contains(k) {
                lines.append("  \(k): \(v)")
            }
        }
        lines.append("")
        lines.append("Open the conversation with a peer-grade briefing on this incident.")
        lines.append("Three to four sentences. Then wait for KK's next question.")
        return lines.joined(separator: "\n")
    }

    private func payloadString(_ key: String, default fallback: String) -> String {
        (incident.payload[key] as? String) ?? fallback
    }

    private var voiceStateColor: Color {
        switch voiceState {
        case .off:        return .gray
        case .connecting: return .yellow
        case .on:         return .green
        }
    }
    private var voiceStateLabel: String {
        switch voiceState {
        case .off:        return "Disconnected"
        case .connecting: return "Connecting Shasta..."
        case .on:         return "Shasta is listening"
        }
    }
}
```

- [ ] **Step 4: Regenerate Xcode project + build + install**

```bash
cd ios
xcodegen generate
xcodebuild build \
  -project CISOCopilot.xcodeproj -scheme CISOCopilot \
  -destination "id=$IOS_DEVICE_UDID" \
  -derivedDataPath build-device \
  -allowProvisioningUpdates
xcrun devicectl device install app --device "$IOS_DEVICE_UDID" \
  build-device/Build/Products/Debug-iphoneos/CISOCopilot.app
```

- [ ] **Step 5: Live smoke test (end-to-end push → voice briefing)**

1. Run a synthetic push test (use the test script from Task 5 with finding context).
2. Tap the notification on the phone.
3. BriefingView appears.
4. After ~300ms, voice connects.
5. Shasta should speak the briefing first, citing fields from the incident payload.

- [ ] **Step 6: Commit**

```bash
git add ios/CISOCopilot/Views/BriefingView.swift \
        ios/CISOCopilot/Services/VoiceClient.swift
git commit -m "feat(ios): briefing auto-mounts voice + seeds incident context

BriefingView.onAppear opens VoiceClient with a developer message
carrying the incident payload. Shasta speaks the briefing first.
Voice state indicator + Done button to disconnect cleanly."
```

---

## Phase 5 — Demo data + dry runs (Days 4–5)

### Task 19: Demo A data staging (Sarah Chen)

**Manual steps for KK + scripted bits:**

- [ ] **Step 1: Create Sarah Chen user in KK's Entra tenant**

KK manual: Entra Admin Center → Users → New user → `sarah.chen@<demo-domain>.transilience.ai` (or similar fictional domain), display name "Sarah Chen", job title "Director of Finance". Set a temporary password.

- [ ] **Step 2: Sign Sarah into ChatGPT (real OAuth)**

KK manual (in an incognito browser):
1. Sign in to portal.office.com as Sarah Chen.
2. Visit chatgpt.com → Sign in with Microsoft → grant consent.
3. Repeat 2 more times across different days to create 3 sign-in events in Entra audit logs (or for demo immediacy, sign in 3 times within an hour — `ai_signin_pass` will see 3 events).

- [ ] **Step 3: Re-run the Entra scan**

```bash
# Either trigger via the Connect page rescan button, or invoke directly:
aws lambda invoke --function-name ciso-copilot-shasta-runner-entra \
  --payload '{"connection_id":"<KK_ENTRA_CONN_ID>","scan_id":"<new-uuid>","tier":"medium"}' \
  /tmp/scan.json
```

Expected: scan completes; `_fire_personal_tier_pushes` fires; KK's iPhone receives "Shadow AI — sarah.chen@... using personal-tier AI app" push.

- [ ] **Step 4: Verify finding lands + push arrives**

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot --sql \
  "SELECT id, title, evidence_packet->>'entra_upn' FROM findings WHERE kind='ai_signin_personal_tier' ORDER BY created_at DESC LIMIT 5"
```

Expected: at least one row with `entra_upn = sarah.chen@...`. Push arrives on KK's phone within ~60s of scan completion.

- [ ] **Step 5: Tap push → BriefingView opens → voice briefs**

Confirm Shasta speaks the briefing in coral voice with peer/expert phrasing, citing Sarah's UPN + the personal-tier classification.

### Task 20: Demo B data staging (vulnerable langchain)

- [ ] **Step 1: Create or use a test GitHub repo on KK's org**

Create `kkmookhey/wow-demo-pricing-system` (or use an existing test repo). Push the following files:

`requirements.txt`:
```
langchain==0.0.184
fastapi>=0.100
pydantic>=2.0
```

`services/pricing/agent.py`:
```python
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from langchain.llms import OpenAI


def make_pricing_chain():
    prompt = PromptTemplate(
        input_variables=["product"],
        template="What is the recommended price for {product}?",
    )
    llm = OpenAI(temperature=0.2)
    return LLMChain(llm=llm, prompt=prompt)
```

- [ ] **Step 2: Manufacture a KEV row for the demo CVE (if needed)**

Check whether a real langchain RCE CVE is already in KEV — if so, use that and tweak the demo script accordingly. If not, insert a synthetic row:

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot --sql \
  "INSERT INTO threat_indicators (value, kind, source, confidence, first_seen, last_seen) \
   VALUES ('CVE-2026-0470', 'cve', 'kev', 95, NOW(), NOW()) \
   ON CONFLICT (value, kind, source) DO NOTHING"
```

- [ ] **Step 3: Connect the repo + run an AI scan**

In the web app: Connect → GitHub → install on `kkmookhey/wow-demo-pricing-system`. Then trigger an AI scan from `/ai`. Expected:
- AI scanner discovers `ai_framework: langchain` and `ai_agent: pricing-agent` with an `imports` edge.
- Trivy emits `sca_vuln` finding for langchain 0.0.184.
- AI scanner enqueues to the matcher queue.
- Matcher joins, emits `ai_supply_chain_active` at CRITICAL, fires push.

- [ ] **Step 4: Verify the finding + push**

```bash
aws rds-data execute-statement \
  --resource-arn $DB_CLUSTER_ARN --secret-arn $DB_SECRET_ARN \
  --database ciso_copilot --sql \
  "SELECT id, title, evidence_packet->>'package', evidence_packet->>'cve' \
   FROM findings WHERE kind='ai_supply_chain_active' ORDER BY created_at DESC LIMIT 5"
```

Expected: row with `package = langchain`, `cve = CVE-2026-0470`. Push arrives on KK's phone with "AI Supply Chain · Critical — KEV CVE in your live pricing-agent (langchain)".

- [ ] **Step 5: Tap push → BriefingView opens → voice briefs**

Confirm Shasta speaks the differentiated insight: *"langchain isn't a dormant dependency. Your pricing-agent actually invokes the LLMChain class at runtime."*

### Task 21: Voice cadence + system prompt iteration + recording

- [ ] **Step 1: Run both demos end-to-end 3-5 times** on KK's iPhone. Note specific failure modes of Coral's delivery — common ones: rushed pace, mispronounced acronyms, slipped into "let me explain" preamble.

- [ ] **Step 2: Iterate the system prompt** based on observed failure modes. Edit `voice_session/system_prompt.py`, redeploy with `npx cdk deploy CisoCopilotApi --hotswap`, repeat. The prompt is hot-swappable in seconds.

- [ ] **Step 3: Record on KK's iPhone** with screen recording. Multiple takes per demo (~5-8); pick best.

- [ ] **Step 4: Edit + post** — captions, intro/outro card, brand lockup. Trim dead air.

- [ ] **Step 5: Final commit**

```bash
git add platform/lambda/voice_session/system_prompt.py   # if iterated
git commit -m "tune(voice): system prompt iteration from recording day

Adjustments observed during 5-8 takes per demo: [...] (fill from notes)"
```

---

## Self-Review

**Spec coverage check:**
- §3 In scope items: all 16 bullets map to tasks above. iOS launch-from-push → Task 5 + 18. Agent-initiated callback push → Tasks 14 + 17. Coral voice + system prompt → Task 2. MCP client + speakable → Tasks 1 + 3. Tools Demo A → Tasks 7, 8, 9. Tools Demo B → Tasks 10, 11, 12, 13. Trivy embedded → Task 4. Matcher Lambda → Task 13. Push triggers → Tasks 15, 16 (via 13), 17. Staged demo data → Tasks 19 + 20. ✓
- §4.2 Demo A "what's real" — uses existing rails, no new code needed for those bits. ✓
- §5.2 Demo B "what's real" — same. ✓
- §6.3 5-day plan: tasks ordered to map to Days 1 → 5 by phase. ✓
- §7 voice persona: Task 2. ✓
- §8 long-identifier distillation: Task 1 + prompt rule in Task 2. ✓
- §9 tool catalog: 1:1 with Tasks 7-13. ✓
- §10 staged data: Tasks 19, 20. ✓
- §11 open items: explicitly out-of-scope-of-plan (recording-day decisions). ✓
- §12 risks: addressed in tasks (e.g., MCP server pinning in Task 3 Step 1, GitHub PR-write scope in Task 10 Step 1).

**Placeholder scan:** no TBDs, no "implement as needed," no "similar to Task N." Each step has actual code or actual commands. ✓

**Type consistency:** speakable_entity / speakable_payload names consistent; ToolRegistryEntry consistent across mcp_client + tools; push functions (`send_push`, `send_push_with_payload`, `tokens_for_tenant`, `notify_tool_completion`) defined in Task 14 and referenced in 13, 15, 17 with matching signatures. ✓

One known soft spot: the CDK code in api-stack.ts / scan-stack.ts is illustrative — the implementer needs to match the existing patterns (property naming, stack-prop access shape, bundling commands). Flagged in Task 6 Step 8 with explicit "match the exact CDK property names by reading api-stack.ts first." Acceptable.
