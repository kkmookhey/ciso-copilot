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
