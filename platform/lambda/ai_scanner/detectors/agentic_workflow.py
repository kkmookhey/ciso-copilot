"""Heuristic detector for agent-shaped functions.

Emits an ``ai_agent`` entity for any function that exhibits ALL three
signals in its body:

  1. A ``while`` loop, OR a recursive self-call.
  2. An LLM SDK call — ``.create(...)`` or ``.invoke_model(...)`` reachable
     in the function body, and the enclosing module imports a known SDK.
  3. A tool-dispatch pattern — references to ``tool_calls`` or
     ``function_call`` or a ``for ... in <something>.tool_calls`` loop.

Heuristic — false positives are expected, hence ``confidence='medium'``.
Each detected function also emits an ``autonomous_loop_no_human_in_loop``
finding (severity: medium, confidence: medium) linked back to the agent
entity.

SP1 natural-key shape (per-file):
  - ai_agent: ``f"{repo_nk}::{rel_path}::{fn_name}"``

No agent→model edge is emitted here — the correlator (T12) emits that as
a colocation pattern.
"""
from __future__ import annotations

import ast
from pathlib import Path

from detectors.base import EntityEmission, FindingEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.agentic_workflow"
detector_version = "0.2.0"

SDK_MARKERS = (
    "from openai", "import openai",
    "from anthropic", "import anthropic",
    "bedrock-runtime", "bedrock_runtime",
)

TOOL_DISPATCH_TOKENS = ("tool_calls", "tool_call", "function_call")


def detect(ctx) -> DetectorResult:
    entities: list[EntityEmission] = []
    findings: list[FindingEmission] = []
    repo_nk = f"github.com/{ctx.repo_full_name}"

    for py in sorted(ctx.repo_workdir.rglob("*.py")):
        try:
            text = py.read_text(errors="ignore")
        except OSError:
            continue
        if not any(m in text for m in SDK_MARKERS):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel_path = str(py.relative_to(ctx.repo_workdir))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _has_loop_or_recursion(node):
                continue
            if not _has_llm_call(node):
                continue
            if not _has_tool_dispatch(node):
                continue
            _emit_agent(ctx, node, rel_path, text, repo_nk, entities, findings)

    return DetectorResult(entities=entities, edges=[], findings=findings)


def _has_loop_or_recursion(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    fn_name = fn.name
    for sub in ast.walk(fn):
        if isinstance(sub, ast.While):
            return True
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                and sub.func.id == fn_name:
            return True
    return False


def _has_llm_call(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if sub.func.attr in ("create", "invoke_model"):
                return True
    return False


def _has_tool_dispatch(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Attribute) and sub.attr in TOOL_DISPATCH_TOKENS:
            return True
        if isinstance(sub, ast.Name) and sub.id in TOOL_DISPATCH_TOKENS:
            return True
    return False


def _emit_agent(ctx, fn: ast.FunctionDef | ast.AsyncFunctionDef, rel_path: str,
                 text: str, repo_nk: str, entities: list, findings: list) -> None:
    name = fn.name
    line = fn.lineno
    snippet = text.splitlines()[line - 1] if 0 < line <= len(text.splitlines()) else ""
    agent_nk = f"{repo_nk}::{rel_path}::{name}"
    packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_asset", subject_type="agent", subject_name=name,
        source_events=[{
            "kind": "file", "repo": ctx.repo_full_name,
            "commit_sha": ctx.head_commit_sha,
            "path": rel_path, "snippet_lines": [line, line],
            "snippet": snippet,
        }],
        reasoning_chain=[
            f"function {name} contains loop + LLM call + tool dispatch at {rel_path}:{line}"
        ],
        confidence="medium",
    )
    entities.append(EntityEmission(
        tenant_id=ctx.tenant_id, kind="ai_agent",
        natural_key=agent_nk, display_name=name, domain="ai",
        attributes={"function": name},
        evidence_packet=packet,
        detector_id=detector_id, detector_version=detector_version,
        connection_id=ctx.connection_id, source_path=rel_path,
    ))

    finding_packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="finding", subject_type="autonomous_loop_no_human_in_loop",
        subject_name=name,
        source_events=[{
            "kind": "file", "repo": ctx.repo_full_name,
            "commit_sha": ctx.head_commit_sha,
            "path": rel_path, "snippet_lines": [line, line],
            "snippet": snippet,
        }],
        reasoning_chain=[
            f"function {name} runs an LLM in a loop with tool dispatch — no explicit human-in-the-loop pause"
        ],
        confidence="medium",
    )
    findings.append(FindingEmission(
        tenant_id=ctx.tenant_id,
        finding_type="autonomous_loop_no_human_in_loop",
        severity="medium",
        title=f"Agent '{name}' runs an autonomous LLM loop without human-in-the-loop",
        description=(
            f"Function {name} in {rel_path} contains a loop, an LLM call, and "
            "tool dispatch. The pattern is consistent with an autonomous agent "
            "with no explicit pause for human review between iterations. "
            "Confirm whether this matches the intended behavior."
        ),
        subject_entity_kind="ai_agent",
        subject_entity_natural_key=agent_nk,
        subject_type=None,
        subject_ref=None,
        evidence_packet=finding_packet,
        confidence="medium",
    ))
