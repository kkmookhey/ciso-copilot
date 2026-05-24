"""Unit tests for the framework registry engine."""
from __future__ import annotations

import json
import pytest
from pathlib import Path

import framework_registry as fr


def test_shipping_registry_loads_and_validates():
    """The shipping JSON must parse and pass schema validation."""
    registry = fr.load_registry()
    assert "frameworks" in registry
    assert "rules" in registry
    assert isinstance(registry["rules"], list)


def test_validate_rejects_rule_with_no_id():
    bad = {"frameworks": {}, "rules": [{"when": {"check_id_eq": "x"}, "add_frameworks": {"x": ["1"]}}]}
    with pytest.raises(fr.RegistryValidationError, match="missing 'id'"):
        fr.validate_registry(bad)


def test_validate_rejects_rule_referencing_unknown_framework():
    bad = {
        "frameworks": {"nist_ai_rmf": {"name": "x", "source": "x", "control_descriptions": {}}},
        "rules": [
            {"id": "r1", "when": {"check_id_eq": "x"}, "add_frameworks": {"made_up_fw": ["X"]}},
        ],
    }
    with pytest.raises(fr.RegistryValidationError, match="unknown framework"):
        fr.validate_registry(bad)


def test_validate_rejects_unknown_selector():
    bad = {
        "frameworks": {"nist_ai_rmf": {"name": "x", "source": "x", "control_descriptions": {}}},
        "rules": [
            {"id": "r1", "when": {"some_unknown_selector": "x"}, "add_frameworks": {"nist_ai_rmf": ["X"]}},
        ],
    }
    with pytest.raises(fr.RegistryValidationError, match="unknown selector"):
        fr.validate_registry(bad)


# --- Selector matching ---

@pytest.fixture
def simple_registry():
    return {
        "frameworks": {
            "nist_ai_rmf": {"name": "x", "source": "x", "control_descriptions": {}},
            "soc2_ai":     {"name": "x", "source": "x", "control_descriptions": {}},
        },
        "rules": [
            {
                "id": "by_check_eq",
                "when": {"check_id_eq": "ai_signin_personal_tier"},
                "add_frameworks": {"nist_ai_rmf": ["GOVERN-1.1"]},
            },
            {
                "id": "by_check_glob",
                "when": {"check_id_glob": "cis_aws_2.1.*"},
                "add_frameworks": {"soc2_ai": ["X.1"]},
            },
            {
                "id": "by_domain",
                "when": {"domain": "ai"},
                "add_frameworks": {"nist_ai_rmf": ["MEASURE-1.1"]},
            },
            {
                "id": "by_resource_type",
                "when": {"resource_type_glob": "aws_bedrock_*"},
                "add_frameworks": {"nist_ai_rmf": ["MEASURE-2.1"]},
            },
            {
                "id": "by_ai_touching",
                "when": {"ai_touching": True},
                "add_frameworks": {"nist_ai_rmf": ["MEASURE-3.1"]},
            },
            {
                "id": "by_evidence",
                "when": {"evidence_packet_eq": {"is_ai": "true"}},
                "add_frameworks": {"nist_ai_rmf": ["MEASURE-4.1"]},
            },
        ],
    }


def _finding(check_id="x", domain=None, resource_type=None, evidence_packet=None,
             subject_entity_id=None, frameworks=None):
    return {
        "check_id":          check_id,
        "domain":            domain,
        "resource_type":     resource_type,
        "evidence_packet":   evidence_packet or {},
        "subject_entity_id": subject_entity_id,
        "frameworks":        frameworks or {},
    }


def test_check_id_eq_matches_exact(simple_registry):
    f = _finding(check_id="ai_signin_personal_tier")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "nist_ai_rmf" in result["frameworks"]
    assert "GOVERN-1.1" in result["frameworks"]["nist_ai_rmf"]


def test_check_id_glob_matches_prefix(simple_registry):
    f = _finding(check_id="cis_aws_2.1.1")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "soc2_ai" in result["frameworks"]


def test_domain_matches(simple_registry):
    f = _finding(domain="ai", check_id="anything")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "MEASURE-1.1" in result["frameworks"]["nist_ai_rmf"]


def test_resource_type_glob_matches(simple_registry):
    f = _finding(resource_type="aws_bedrock_endpoint", check_id="anything")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "MEASURE-2.1" in result["frameworks"]["nist_ai_rmf"]


def test_ai_touching_via_evidence_packet(simple_registry):
    f = _finding(evidence_packet={"is_ai": "true"}, check_id="anything")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    # Both by_ai_touching and by_evidence rules fire.
    assert "MEASURE-3.1" in result["frameworks"]["nist_ai_rmf"]
    assert "MEASURE-4.1" in result["frameworks"]["nist_ai_rmf"]


def test_ai_touching_via_entity_domain(simple_registry):
    f = _finding(subject_entity_id="abc-123", check_id="anything")
    entity_index = {"abc-123": {"domain": "ai", "kind": "bedrock_model"}}
    result = fr.apply(f, entity_index=entity_index, registry=simple_registry)
    assert "MEASURE-3.1" in result["frameworks"]["nist_ai_rmf"]


def test_ai_touching_false_when_entity_missing(simple_registry):
    """If subject_entity_id points to a missing entity, ai_touching is False (not error)."""
    f = _finding(subject_entity_id="missing", check_id="anything")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "MEASURE-3.1" not in result["frameworks"].get("nist_ai_rmf", [])


def test_evidence_packet_eq_match(simple_registry):
    f = _finding(evidence_packet={"is_ai": "true"}, check_id="anything")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "MEASURE-4.1" in result["frameworks"]["nist_ai_rmf"]


def test_no_rule_matches_no_op(simple_registry):
    f = _finding(check_id="random_check", frameworks={"soc2": ["CC1.1"]})
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert result["frameworks"] == {"soc2": ["CC1.1"]}


# --- Additive merge + idempotency ---

def test_existing_frameworks_preserved(simple_registry):
    """Shasta-emitted controls must not be overwritten."""
    f = _finding(check_id="ai_signin_personal_tier",
                 frameworks={"nist_ai_rmf": ["MANAGE-1.0"]})
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert "MANAGE-1.0" in result["frameworks"]["nist_ai_rmf"]
    assert "GOVERN-1.1" in result["frameworks"]["nist_ai_rmf"]
    # Sorted for diff stability.
    assert result["frameworks"]["nist_ai_rmf"] == sorted(result["frameworks"]["nist_ai_rmf"])


def test_duplicate_controls_deduped(simple_registry):
    """Re-apply produces same output."""
    f = _finding(check_id="ai_signin_personal_tier",
                 frameworks={"nist_ai_rmf": ["GOVERN-1.1"]})
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert result["frameworks"]["nist_ai_rmf"].count("GOVERN-1.1") == 1


def test_idempotency(simple_registry):
    """apply(apply(f)) == apply(f)."""
    f = _finding(check_id="ai_signin_personal_tier")
    once = fr.apply(dict(f, frameworks={}), entity_index={}, registry=simple_registry)
    twice = fr.apply(once, entity_index={}, registry=simple_registry)
    assert once["frameworks"] == twice["frameworks"]
    assert once["evidence_packet"]["_registry_rule_ids"] == twice["evidence_packet"]["_registry_rule_ids"]


def test_provenance_rule_ids_recorded(simple_registry):
    f = _finding(check_id="ai_signin_personal_tier")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert result["evidence_packet"]["_registry_rule_ids"] == ["by_check_eq"]


def test_provenance_multiple_rules_recorded(simple_registry):
    f = _finding(check_id="ai_signin_personal_tier", domain="ai")
    result = fr.apply(f, entity_index={}, registry=simple_registry)
    assert sorted(result["evidence_packet"]["_registry_rule_ids"]) == ["by_check_eq", "by_domain"]


# --- Slice E integration tests against the shipping registry ---


def test_personal_tier_finding_tagged_with_ai_frameworks():
    f = _finding(check_id="ai_signin_personal_tier")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "GOVERN 3.2" in result["frameworks"].get("nist_ai_rmf", [])
    assert "GOVERN 6.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "GOVERN 1.6" in result["frameworks"].get("nist_ai_600_1", [])
    assert "Article 9" in result["frameworks"].get("eu_ai_act", [])
    assert "Article 26" in result["frameworks"].get("eu_ai_act", [])
    assert "LLM02:2025" in result["frameworks"].get("owasp_llm_top10", [])
    assert "AML.T0057" in result["frameworks"].get("mitre_atlas", [])


def test_corp_tier_finding_tagged():
    f = _finding(check_id="ai_signin_corp_tier")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "GOVERN 1.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MAP 1.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "GOVERN 1.6" in result["frameworks"].get("nist_ai_600_1", [])
    assert "Article 9" in result["frameworks"].get("eu_ai_act", [])
    # corp_tier should NOT carry Article 26 (deployer obligations for high-risk
    # systems) — only the unknown/personal tiers do
    assert "Article 26" not in result["frameworks"].get("eu_ai_act", [])


def test_unknown_tier_finding_tagged():
    f = _finding(check_id="ai_signin_unknown_tier")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "MAP 1.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MAP 5.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "Article 9" in result["frameworks"].get("eu_ai_act", [])
    assert "LLM02:2025" in result["frameworks"].get("owasp_llm_top10", [])


def test_unrelated_finding_not_tagged_by_entra_rules():
    """A cloud finding (e.g., AWS S3) must not pick up the Entra-specific rules."""
    f = _finding(check_id="cis_aws_2.1.1")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    # None of the Entra rules' frameworks should fire for an AWS finding.
    assert "Article 9" not in result["frameworks"].get("eu_ai_act", [])
    assert "GOVERN 3.2" not in result["frameworks"].get("nist_ai_rmf", [])


def test_provenance_records_correct_rule_id_for_personal_tier():
    f = _finding(check_id="ai_signin_personal_tier")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "ai_signin_personal_tier_controls" in result["evidence_packet"]["_registry_rule_ids"]


# --- CME-v2 S1: extended schema acceptance ---


def test_validator_accepts_new_optional_fields():
    """Frameworks with family/source_url/version/canonical_format/rewrite_rules validate."""
    registry = {
        "frameworks": {
            "nist_ai_rmf": {
                "name":             "NIST AI RMF",
                "family":           "ai",
                "source":           "NIST AI 100-1 (2023)",
                "source_url":       "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf",
                "version":          "1.0",
                "canonical_format": "FUNCTION SUBCATEGORY (space)",
                "rewrite_rules":    [],
                "control_descriptions": {},
            },
        },
        "rules": [],
    }
    fr.validate_registry(registry)  # Should not raise


def test_validator_rejects_rewrite_rule_with_no_from():
    bad = {
        "frameworks": {
            "nist_ai_rmf": {
                "name": "x", "family": "ai", "source": "x",
                "control_descriptions": {},
                "rewrite_rules": [{"to": ["GOVERN 6.1"]}],  # missing 'from'
            },
        },
        "rules": [],
    }
    with pytest.raises(fr.RegistryValidationError, match="missing 'from'"):
        fr.validate_registry(bad)


def test_validator_rejects_rewrite_rule_with_empty_to():
    bad = {
        "frameworks": {
            "nist_ai_rmf": {
                "name": "x", "family": "ai", "source": "x",
                "control_descriptions": {},
                "rewrite_rules": [{"from": "GOVERN-6", "to": []}],
            },
        },
        "rules": [],
    }
    with pytest.raises(fr.RegistryValidationError, match="'to' must be a non-empty"):
        fr.validate_registry(bad)


def test_validator_rejects_invalid_family():
    bad = {
        "frameworks": {
            "nist_ai_rmf": {
                "name": "x", "family": "made_up_family", "source": "x",
                "control_descriptions": {},
            },
        },
        "rules": [],
    }
    with pytest.raises(fr.RegistryValidationError, match="unknown family"):
        fr.validate_registry(bad)


def test_validator_requires_family_on_every_framework():
    """Spec §5: every framework must declare a family."""
    bad = {
        "frameworks": {
            "no_family": {
                "name": "x", "source": "x", "control_descriptions": {},
                # 'family' deliberately omitted
            },
        },
        "rules": [],
    }
    with pytest.raises(fr.RegistryValidationError, match="missing 'family'"):
        fr.validate_registry(bad)


# --- CME-v2 S1: _normalize_stage ---


@pytest.fixture
def normalize_test_registry():
    """A registry with rewrite_rules covering several patterns for normalize tests."""
    return {
        "frameworks": {
            "nist_ai_rmf": {
                "name": "x", "family": "ai", "source": "x",
                "control_descriptions": {},
                "rewrite_rules": [
                    {"from": "GOVERN-6", "to": ["GOVERN 6.1", "GOVERN 6.2", "GOVERN 6.3"]},
                    {"from": "MANAGE-3", "to": ["MANAGE 3.1", "MANAGE 3.2"]},
                ],
            },
            "owasp_llm_top10": {
                "name": "x", "family": "ai", "source": "x",
                "control_descriptions": {},
                "rewrite_rules": [
                    {"from": "LLM01", "to": ["LLM01:2025"]},
                    {"from": "LLM05", "to": ["LLM05:2025"]},
                ],
            },
            "soc2": {
                "name": "x", "family": "security", "source": "x",
                "control_descriptions": {},
                # No rewrite_rules at all — passthrough framework
            },
        },
        "rules": [],
    }


def test_normalize_no_op_when_finding_has_no_frameworks(normalize_test_registry):
    f = _finding(check_id="x", frameworks={})
    fr._normalize_stage(f, registry=normalize_test_registry)
    assert f["frameworks"] == {}


def test_normalize_rewrites_one_to_many(normalize_test_registry):
    """GOVERN-6 expands to three canonical subcategories."""
    f = _finding(check_id="x", frameworks={"nist_ai_rmf": ["GOVERN-6"]})
    fr._normalize_stage(f, registry=normalize_test_registry)
    assert f["frameworks"]["nist_ai_rmf"] == ["GOVERN 6.1", "GOVERN 6.2", "GOVERN 6.3"]


def test_normalize_rewrites_one_to_one(normalize_test_registry):
    """LLM01 becomes LLM01:2025."""
    f = _finding(check_id="x", frameworks={"owasp_llm_top10": ["LLM01"]})
    fr._normalize_stage(f, registry=normalize_test_registry)
    assert f["frameworks"]["owasp_llm_top10"] == ["LLM01:2025"]


def test_normalize_passes_through_unknown_id(normalize_test_registry):
    """An ID with no matching rewrite rule stays as-is."""
    f = _finding(check_id="x", frameworks={"nist_ai_rmf": ["UNKNOWN-42"]})
    fr._normalize_stage(f, registry=normalize_test_registry)
    assert f["frameworks"]["nist_ai_rmf"] == ["UNKNOWN-42"]


def test_normalize_passes_through_framework_without_rewrite_rules(normalize_test_registry):
    """A framework with no rewrite_rules block leaves its tags untouched."""
    f = _finding(check_id="x", frameworks={"soc2": ["CC6.1"]})
    fr._normalize_stage(f, registry=normalize_test_registry)
    assert f["frameworks"]["soc2"] == ["CC6.1"]


def test_normalize_handles_mixed_known_and_unknown_in_same_list(normalize_test_registry):
    """A list with both rewritable and passthrough IDs: rewritable expands, unknown stays."""
    f = _finding(check_id="x", frameworks={"nist_ai_rmf": ["GOVERN-6", "UNKNOWN-99"]})
    fr._normalize_stage(f, registry=normalize_test_registry)
    # Result: union of GOVERN-6's expansion + UNKNOWN-99, sorted
    assert f["frameworks"]["nist_ai_rmf"] == ["GOVERN 6.1", "GOVERN 6.2", "GOVERN 6.3", "UNKNOWN-99"]


def test_normalize_idempotency(normalize_test_registry):
    """Applying normalize twice yields the same result."""
    f = _finding(check_id="x", frameworks={"nist_ai_rmf": ["GOVERN-6"]})
    fr._normalize_stage(f, registry=normalize_test_registry)
    once = list(f["frameworks"]["nist_ai_rmf"])
    fr._normalize_stage(f, registry=normalize_test_registry)
    twice = list(f["frameworks"]["nist_ai_rmf"])
    assert once == twice


def test_normalize_handles_multiple_rewrites_in_same_framework(normalize_test_registry):
    """Two rewritable IDs in the same framework both expand."""
    f = _finding(check_id="x", frameworks={"nist_ai_rmf": ["GOVERN-6", "MANAGE-3"]})
    fr._normalize_stage(f, registry=normalize_test_registry)
    assert f["frameworks"]["nist_ai_rmf"] == [
        "GOVERN 6.1", "GOVERN 6.2", "GOVERN 6.3",
        "MANAGE 3.1", "MANAGE 3.2",
    ]
