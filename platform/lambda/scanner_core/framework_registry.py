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


# Loaded once at module import. Failures fail the Lambda cold-start.
_REGISTRY: dict = load_registry()
