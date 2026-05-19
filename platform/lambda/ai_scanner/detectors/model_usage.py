"""Detect calls to commercial LLM SDKs (OpenAI, Anthropic, Bedrock).

Strategy: scan .py files for `model="..."` (or `modelId="..."`) strings AND
JSON-style `"model": "..."` literals and emit one asset per (file, provider,
model_id) tuple. The file must also carry SOME provider signal — either an
SDK import or a hard-coded API URL — to keep false positives down (a
deterministic security scanner shouldn't flag every config file that
mentions the word "model").
"""
from __future__ import annotations

import re

from detectors.base import AssetEmission, RelEmission, DetectorResult
import evidence as ev

detector_id      = "ai.detectors.model_usage"
detector_version = "0.2.0"

# Provider signals: any of these substrings → consider the file in scope for
# that provider. Covers both SDK imports and raw-HTTPS usage of the API.
PROVIDER_SIGNALS = [
    # (signal_substring, provider, model_kwarg)
    ("from openai",        "openai",    "model"),
    ("import openai",      "openai",    "model"),
    ("api.openai.com",     "openai",    "model"),
    ("from anthropic",     "anthropic", "model"),
    ("import anthropic",   "anthropic", "model"),
    ("api.anthropic.com",  "anthropic", "model"),
    ("bedrock-runtime",    "bedrock",   "modelId"),
    ("bedrock_runtime",    "bedrock",   "modelId"),
]

# Model-id strings we recognize even without a provider signal (Bedrock
# uses "anthropic.claude-*", Anthropic SDK uses "claude-*", OpenAI uses
# "gpt-*"). Used to disambiguate provider in raw-HTTPS code that doesn't
# carry one of the strings above.
MODEL_ID_PROVIDER_HINTS = [
    (re.compile(r"^anthropic\.claude"), "bedrock"),
    (re.compile(r"^claude-"),           "anthropic"),
    (re.compile(r"^gpt-"),              "openai"),
]


def detect(ctx) -> DetectorResult:
    assets: list[AssetEmission] = []
    rels:   list[RelEmission] = []
    seen: set[tuple[str, str, str]] = set()  # (path, provider, model)

    py_files = sorted(ctx.repo_workdir.rglob("*.py"))
    for f in py_files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        rel_path = str(f.relative_to(ctx.repo_workdir))

        # Which providers does this file appear to reference?
        file_providers: dict[str, str] = {}  # provider -> model_kwarg
        for marker, provider, model_kwarg in PROVIDER_SIGNALS:
            if marker in text:
                file_providers.setdefault(provider, model_kwarg)
        if not file_providers:
            continue

        # Build a combined regex matching BOTH styles:
        #   kwarg style: model="gpt-4o"        (Python SDK call)
        #   json   style: "model": "gpt-4o"    (raw HTTPS via json.dumps)
        for provider, model_kwarg in file_providers.items():
            kwarg_pat = re.compile(rf'\b{model_kwarg}\s*=\s*["\']([^"\']+)["\']')
            json_pat  = re.compile(rf'["\']{model_kwarg}["\']\s*:\s*["\']([^"\']+)["\']')
            for pat in (kwarg_pat, json_pat):
                for m in pat.finditer(text):
                    model_id = m.group(1)
                    # If the model id has a strong provider hint, trust it
                    # over the signal-derived provider (e.g. a file with
                    # "bedrock-runtime" + a "claude-3-5-sonnet" string is
                    # still bedrock, not anthropic-direct).
                    resolved = _resolve_provider(provider, model_id)
                    key = (rel_path, resolved, model_id)
                    if key in seen:
                        continue
                    seen.add(key)

                    line_no = text[:m.start()].count("\n") + 1
                    lines = text.splitlines()
                    snippet = lines[line_no - 1].strip() if 0 < line_no <= len(lines) else ""

                    packet = ev.build(
                        detector_id=detector_id, detector_version=detector_version,
                        subject_kind="ai_asset", subject_type="model",
                        subject_name=f"{resolved}/{model_id}",
                        source_events=[{
                            "kind": "file", "repo": ctx.repo_full_name,
                            "commit_sha": ctx.head_commit_sha,
                            "path": rel_path, "snippet_lines": [line_no, line_no],
                            "snippet": snippet,
                        }],
                        reasoning_chain=[
                            f"matched model_id \"{model_id}\" in {resolved}-using file at {rel_path}:{line_no}"
                        ],
                        confidence="high",
                    )
                    assets.append(AssetEmission(
                        tenant_id=ctx.tenant_id, connection_id=ctx.connection_id,
                        asset_type="model", name=f"{resolved}/{model_id}",
                        source_repo_id=ctx.repo_asset_id, source_path=rel_path,
                        attributes={"provider": resolved, "model_id": model_id},
                        evidence_packet=packet,
                        detector_id=detector_id, detector_version=detector_version,
                    ))

                    rel_packet = ev.build(
                        detector_id=detector_id, detector_version=detector_version,
                        subject_kind="ai_relationship", subject_type="calls",
                        subject_name=f"repo→calls→{resolved}/{model_id}",
                        source_events=[],
                        reasoning_chain=["model use detected in repo"],
                        confidence="high",
                    )
                    rels.append(RelEmission(
                        tenant_id=ctx.tenant_id,
                        source_asset_ref=f"repository::::{ctx.repo_asset_id}",
                        target_asset_ref=f"model::{ctx.repo_asset_id}::{rel_path}::{resolved}/{model_id}",
                        relationship_type="calls",
                        attributes={"provider": resolved},
                        evidence_packet=rel_packet,
                        detector_id=detector_id, detector_version=detector_version,
                    ))

    return DetectorResult(assets=assets, relationships=rels, findings=[])


def _resolve_provider(default_provider: str, model_id: str) -> str:
    """Prefer the provider hinted by the model id itself; fall back to the
    provider derived from the file's signal markers."""
    for pat, prov in MODEL_ID_PROVIDER_HINTS:
        if pat.match(model_id):
            return prov
    return default_provider
