"""Detect prompts: dedicated prompt files + long inline strings in SDK calls.

File signals:
  - files under ``prompts/``
  - files matching ``prompt*.{txt,md}``
  - files with extension ``.prompt``

Inline signals (Python only):
  - multi-line string constants (>200 chars) passed as ``prompt=`` or
    ``system=`` kwargs to a ``.create(...)`` call.

For every detected prompt, emits one ``ai_prompt`` entity + a
``github_repo → accesses → ai_prompt`` edge. If the prompt body matches a
known secret regex, additionally emits a ``prompt_with_secret_pattern``
finding (HIGH) linked back to the prompt entity.

SP1 natural-key shape (per-file):
  - file prompt:    name = rel_path,           nk = f"{repo_nk}::{rel_path}::{name}"
  - inline prompt:  name = f"{kwarg}@{line}",  nk = f"{repo_nk}::{rel_path}::{name}"
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from detectors.base import EntityEmission, EdgeEmission, FindingEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.prompt"
detector_version = "0.2.0"

PROMPT_KWARGS = ("prompt", "system", "instructions")
INLINE_MIN_CHARS = 200

SECRET_PATTERNS = [
    ("openai",    re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_\-]{40,}")),
    ("slack",     re.compile(r"xoxb-[A-Za-z0-9\-]{20,}")),
    ("aws",       re.compile(r"AKIA[0-9A-Z]{16}")),
]


def detect(ctx) -> DetectorResult:
    entities: list[EntityEmission] = []
    edges:    list[EdgeEmission] = []
    findings: list[FindingEmission] = []
    repo_nk = f"github.com/{ctx.repo_full_name}"

    for f in _prompt_files(ctx.repo_workdir):
        try:
            body = f.read_text(errors="ignore")
        except OSError:
            continue
        rel_path = str(f.relative_to(ctx.repo_workdir))
        _emit_prompt(ctx, name=rel_path, body=body, rel_path=rel_path,
                      line=1, repo_nk=repo_nk,
                      entities=entities, edges=edges, findings=findings,
                      kind="file")

    for py in sorted(ctx.repo_workdir.rglob("*.py")):
        try:
            text = py.read_text(errors="ignore")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        rel_path = str(py.relative_to(ctx.repo_workdir))
        for inline in _inline_prompts(tree):
            name = f"{inline['kwarg']}@{inline['line']}"
            _emit_prompt(ctx, name=name, body=inline["body"], rel_path=rel_path,
                          line=inline["line"], repo_nk=repo_nk,
                          entities=entities, edges=edges, findings=findings,
                          kind="inline")

    return DetectorResult(entities=entities, edges=edges, findings=findings)


def _prompt_files(root: Path) -> list[Path]:
    seen: set[Path] = set()
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(root)
        parts = rel.parts
        if "prompts" in parts:
            seen.add(f)
            continue
        if f.suffix == ".prompt":
            seen.add(f)
            continue
        if f.name.startswith("prompt") and f.suffix in (".txt", ".md"):
            seen.add(f)
            continue
    return sorted(seen)


def _inline_prompts(tree: ast.AST) -> list[dict]:
    out: list[dict] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create"):
            continue
        for kw in node.keywords:
            if kw.arg not in PROMPT_KWARGS:
                continue
            if not (isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)):
                continue
            body = kw.value.value
            if len(body) < INLINE_MIN_CHARS:
                continue
            out.append({"kwarg": kw.arg, "body": body, "line": kw.value.lineno})
    return out


def _emit_prompt(ctx, *, name: str, body: str, rel_path: str, line: int,
                  repo_nk: str, entities: list, edges: list, findings: list,
                  kind: str) -> None:
    prompt_nk = f"{repo_nk}::{rel_path}::{name}"
    snippet = body[:120].replace("\n", " ")
    packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_asset", subject_type="prompt", subject_name=name,
        source_events=[{
            "kind": "file", "repo": ctx.repo_full_name,
            "commit_sha": ctx.head_commit_sha,
            "path": rel_path, "snippet_lines": [line, line],
            "snippet": snippet,
        }],
        reasoning_chain=[f"detected {kind} prompt at {rel_path}:{line}"],
        confidence="high",
    )
    entities.append(EntityEmission(
        tenant_id=ctx.tenant_id, kind="ai_prompt",
        natural_key=prompt_nk, display_name=name, domain="ai",
        attributes={"kind": kind, "chars": len(body)},
        evidence_packet=packet,
        detector_id=detector_id, detector_version=detector_version,
        connection_id=ctx.connection_id, source_path=rel_path,
    ))

    rel_packet = ev.build(
        detector_id=detector_id, detector_version=detector_version,
        subject_kind="ai_relationship", subject_type="accesses",
        subject_name=f"repo→accesses→{name}",
        source_events=[], reasoning_chain=["prompt detected in repo"],
        confidence="high",
    )
    edges.append(EdgeEmission(
        tenant_id=ctx.tenant_id,
        source_kind="github_repo", source_natural_key=repo_nk,
        target_kind="ai_prompt", target_natural_key=prompt_nk,
        kind="accesses", attributes={},
        evidence_packet=rel_packet,
        detector_id=detector_id, detector_version=detector_version,
    ))

    leaks = _leaked_secrets(body)
    if leaks:
        finding_packet = ev.build(
            detector_id=detector_id, detector_version=detector_version,
            subject_kind="finding", subject_type="prompt_with_secret_pattern",
            subject_name=name,
            source_events=[{
                "kind": "file", "repo": ctx.repo_full_name,
                "commit_sha": ctx.head_commit_sha,
                "path": rel_path, "snippet_lines": [line, line],
                "snippet": "(secret redacted)",
            }],
            reasoning_chain=[
                f"prompt body matched secret patterns: {sorted(leaks)}"
            ],
            confidence="high",
        )
        findings.append(FindingEmission(
            tenant_id=ctx.tenant_id,
            finding_type="prompt_with_secret_pattern",
            severity="high",
            title=f"Prompt at {rel_path} contains secret-shaped strings",
            description=(
                f"The prompt at {rel_path} matches one or more secret regexes "
                f"({sorted(leaks)}). Prompts are often logged to model providers, "
                "third-party observability tooling, and persistent storage — "
                "shipping a real key inside a prompt is effectively leaking it. "
                "Rotate the credential and remove it from the prompt."
            ),
            subject_entity_kind="ai_prompt",
            subject_entity_natural_key=prompt_nk,
            subject_type=None,
            subject_ref=None,
            evidence_packet=finding_packet,
            confidence="high",
        ))


def _leaked_secrets(body: str) -> list[str]:
    hit: list[str] = []
    for label, pat in SECRET_PATTERNS:
        if pat.search(body):
            hit.append(label)
    return hit
