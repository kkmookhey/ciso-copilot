"""Framework registry engine — applies compliance crosswalk to findings.

See docs/superpowers/specs/2026-05-24-ai-visibility-v2-slice-3-design.md
for design rationale.
"""
from __future__ import annotations

import fnmatch
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

_KNOWN_SELECTORS = frozenset({
    "check_id_eq",
    "check_id_glob",
    "domain",
    "resource_type_glob",
    "ai_touching",
    "evidence_packet_eq",
})

_KNOWN_FAMILIES = frozenset({"security", "ai", "privacy", "industry"})

_REGISTRY_PATH = Path(__file__).parent / "ai_framework_registry.json"

# AI-touching entity kinds — mirrors ai_summary._AI_RESOURCE_KINDS.
# Used by the ai_touching selector. Kept in code so deploys carry the truth.
_AI_RESOURCE_KINDS = frozenset({
    "bedrock_model", "bedrock_guardrail", "sagemaker_endpoint",
    "sagemaker_model", "sagemaker_training_job", "comprehend_endpoint",
    "lambda_ai_function",
    "azure_openai_deployment", "azure_ml_workspace", "cognitive_service",
    "vertex_endpoint",
    "ai_saas_app", "ai_code_finding",
    "ai_user_signin", "ai_api_key", "ai_org_member", "ai_project",
    "ai_provider_org",
    "ai_agent", "ai_embedding", "ai_framework", "ai_mcp_server",
    "ai_model", "ai_prompt", "ai_tool", "ai_vector_db",
})


class RegistryValidationError(Exception):
    """Raised at module load / image build if the registry JSON is malformed."""


class RegistryApplyError(Exception):
    """Raised by apply() if a rule's selector fails. Caller wraps in try/except."""


def load_registry(path: Path | None = None) -> dict:
    """Load + validate the registry. Called once at module import."""
    target = path or _REGISTRY_PATH
    with open(target) as f:
        registry = json.load(f)
    validate_registry(registry)
    return registry


def validate_registry(registry: dict) -> None:
    """Schema validation. Raises RegistryValidationError on any defect."""
    if "frameworks" not in registry or not isinstance(registry["frameworks"], dict):
        raise RegistryValidationError("missing or invalid 'frameworks' block")
    if "rules" not in registry or not isinstance(registry["rules"], list):
        raise RegistryValidationError("missing or invalid 'rules' block")

    known_fws = set(registry["frameworks"].keys())

    for i, rule in enumerate(registry["rules"]):
        ctx = f"rule[{i}]"
        if "id" not in rule:
            raise RegistryValidationError(f"{ctx}: missing 'id'")
        if "when" not in rule or not isinstance(rule["when"], dict) or not rule["when"]:
            raise RegistryValidationError(f"rule[{rule.get('id', i)}]: 'when' must be a non-empty dict")
        if "add_frameworks" not in rule or not isinstance(rule["add_frameworks"], dict) or not rule["add_frameworks"]:
            raise RegistryValidationError(f"rule[{rule['id']}]: 'add_frameworks' must be a non-empty dict")

        unknown_sel = set(rule["when"].keys()) - _KNOWN_SELECTORS
        if unknown_sel:
            raise RegistryValidationError(
                f"rule[{rule['id']}]: unknown selector(s) {sorted(unknown_sel)}"
            )

        unknown_fw = set(rule["add_frameworks"].keys()) - known_fws
        if unknown_fw:
            raise RegistryValidationError(
                f"rule[{rule['id']}]: unknown framework(s) {sorted(unknown_fw)} in add_frameworks"
            )

    # CME-v2 S1: validate per-framework metadata + rewrite_rules
    for fw_key, fw in registry["frameworks"].items():
        if not isinstance(fw, dict):
            raise RegistryValidationError(f"framework[{fw_key}]: must be an object")

        # CME-v2 §5: family is mandatory.
        if "family" not in fw:
            raise RegistryValidationError(f"framework[{fw_key}]: missing 'family'")
        family = fw["family"]
        if family not in _KNOWN_FAMILIES:
            raise RegistryValidationError(
                f"framework[{fw_key}]: unknown family {family!r}; "
                f"must be one of {sorted(_KNOWN_FAMILIES)}"
            )

        # rewrite_rules is optional; if present, must be a list of well-formed entries.
        rewrite_rules = fw.get("rewrite_rules")
        if rewrite_rules is not None:
            if not isinstance(rewrite_rules, list):
                raise RegistryValidationError(
                    f"framework[{fw_key}]: 'rewrite_rules' must be a list"
                )
            for i, rr in enumerate(rewrite_rules):
                ctx = f"framework[{fw_key}].rewrite_rules[{i}]"
                if not isinstance(rr, dict):
                    raise RegistryValidationError(f"{ctx}: must be an object")
                if "from" not in rr:
                    raise RegistryValidationError(f"{ctx}: missing 'from'")
                if not isinstance(rr.get("from"), str) or not rr["from"]:
                    raise RegistryValidationError(f"{ctx}: 'from' must be a non-empty string")
                to = rr.get("to")
                if not isinstance(to, list) or not to:
                    raise RegistryValidationError(f"{ctx}: 'to' must be a non-empty list")
                for t in to:
                    if not isinstance(t, str) or not t:
                        raise RegistryValidationError(
                            f"{ctx}: every 'to' entry must be a non-empty string"
                        )


# Loaded once at module import. Failures fail the Lambda cold-start.
_REGISTRY: dict = load_registry()

# ----- Selector matching + apply() -----


def _matches(finding: dict, entity_index: dict, when: dict) -> bool:
    """All selectors AND-ed together. False on any miss."""
    for selector, expected in when.items():
        if selector == "check_id_eq":
            if finding.get("check_id") != expected:
                return False
        elif selector == "check_id_glob":
            if not fnmatch.fnmatchcase(finding.get("check_id") or "", expected):
                return False
        elif selector == "domain":
            if finding.get("domain") != expected:
                return False
        elif selector == "resource_type_glob":
            if not fnmatch.fnmatchcase(finding.get("resource_type") or "", expected):
                return False
        elif selector == "ai_touching":
            actual = _is_ai_touching(finding, entity_index)
            if actual != expected:
                return False
        elif selector == "evidence_packet_eq":
            ep = finding.get("evidence_packet") or {}
            for k, v in expected.items():
                if str(ep.get(k)) != str(v):
                    return False
        else:
            # Should be caught by validation; defensive.
            raise RegistryApplyError(f"unknown selector at apply time: {selector}")
    return True


def _is_ai_touching(finding: dict, entity_index: dict) -> bool:
    """Mirrors ai_summary._IS_AI_TOUCHING predicate.

    A finding is AI-touching if:
      - subject entity has domain='ai', OR
      - subject entity has an AI-resource kind, OR
      - evidence_packet ->> 'is_ai' = 'true'.

    Framework-key match is NOT used here (that would be circular with apply()).
    """
    ep = finding.get("evidence_packet") or {}
    if str(ep.get("is_ai")) == "true":
        return True
    eid = finding.get("subject_entity_id")
    if not eid:
        return False
    entity = entity_index.get(eid)
    if not entity:
        return False
    if entity.get("domain") == "ai":
        return True
    if entity.get("kind") in _AI_RESOURCE_KINDS:
        return True
    return False


def _normalize_stage(finding: dict, registry: dict | None = None) -> dict:
    """Stage 1 of the CME-v2 pipeline: rewrite scanner-emitted control IDs
    to canonical published format using each framework's rewrite_rules.

    Mutates finding['frameworks'] in place. Idempotent: re-running on an
    already-normalized finding produces identical output.

    For each (framework_key, control_ids) pair on the finding:
      - Look up the framework's rewrite_rules (default empty list).
      - For each control_id:
        - If a rewrite rule has from == control_id, replace with rule.to entries.
        - Else, passthrough (the ID stays).
      - Result is the set-union of all rewritten + passthrough IDs, sorted.
    """
    reg = registry if registry is not None else _REGISTRY
    frameworks_block = reg.get("frameworks", {})

    for fw_key, ctrls in list(finding.get("frameworks", {}).items()):
        fw_def = frameworks_block.get(fw_key, {})
        rules = fw_def.get("rewrite_rules") or []
        if not rules:
            continue  # No rewrite rules for this framework — passthrough

        # Build a {from: to-list} dict for O(1) lookup
        rewrite_map: dict[str, list[str]] = {}
        for rr in rules:
            rewrite_map.setdefault(rr["from"], []).extend(rr["to"])

        normalized: set[str] = set()
        for cid in (ctrls or []):
            if cid in rewrite_map:
                normalized.update(rewrite_map[cid])
            else:
                normalized.add(cid)

        finding["frameworks"][fw_key] = sorted(normalized)

    return finding


def _augment_stage(finding: dict, entity_index: dict, registry: dict | None = None) -> dict:
    """Stage 2 of the CME-v2 pipeline: walk registry rules, set-union merge
    matching add_frameworks into finding's frameworks, record provenance.

    This is the Slice 3 apply() logic, unchanged. Renamed so apply() can
    orchestrate it after _normalize_stage.
    """
    reg = registry if registry is not None else _REGISTRY
    rules_fired: list[str] = []

    for rule in reg["rules"]:
        try:
            if _matches(finding, entity_index, rule["when"]):
                rules_fired.append(rule["id"])
                for fw, ctrls in rule["add_frameworks"].items():
                    existing = set(finding["frameworks"].get(fw) or [])
                    finding["frameworks"][fw] = sorted(existing | set(ctrls))
        except RegistryApplyError:
            raise

    if rules_fired:
        ep = finding.setdefault("evidence_packet", {})
        prior = set(ep.get("_registry_rule_ids") or [])
        ep["_registry_rule_ids"] = sorted(prior | set(rules_fired))

    return finding


def apply(finding: dict, entity_index: dict, registry: dict | None = None) -> dict:
    """CME-v2 two-stage compliance crosswalk.

    Stage 1 (normalize): rewrite scanner-emitted control IDs to canonical
                          published format using each framework's rewrite_rules.
    Stage 2 (augment):   walk registry rules, additively merge matching
                          add_frameworks into finding's frameworks.

    Mutates finding in place. Additive across runs. Idempotent.
    """
    _normalize_stage(finding, registry=registry)
    _augment_stage(finding, entity_index, registry=registry)
    return finding
