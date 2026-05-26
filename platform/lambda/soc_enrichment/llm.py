"""LiteLLM wrapper. Model is config-driven via SOC_ENRICHMENT_LLM_MODEL."""
from __future__ import annotations
import json
import os
import sys

import boto3
import litellm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spend_cap  # type: ignore


MODEL = os.environ.get("SOC_ENRICHMENT_LLM_MODEL", "claude-sonnet-4-6")
DAILY_CAP_CENTS_DEFAULT = int(os.environ.get("SOC_ENRICHMENT_DAILY_CAP_CENTS", "1000"))  # $10/day

# Anthropic per-million-token pricing for cost estimation
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
        "event": {k: row.get(k) for k in ("source", "kind", "severity", "title", "actor",
                                           "resource_arn", "fired_at", "after_state", "before_state")},
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
