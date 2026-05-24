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
    # S3: replaced leaked RMF subcategory format with canonical GAI-N identifiers
    assert "GAI-2" in result["frameworks"].get("nist_ai_600_1", [])
    assert "GAI-8" in result["frameworks"].get("nist_ai_600_1", [])
    assert "GAI-9" in result["frameworks"].get("nist_ai_600_1", [])
    assert "GOVERN 1.6" not in result["frameworks"].get("nist_ai_600_1", [])
    assert "Article 9" in result["frameworks"].get("eu_ai_act", [])
    assert "Article 26" in result["frameworks"].get("eu_ai_act", [])
    assert "LLM02:2025" in result["frameworks"].get("owasp_llm_top10", [])
    assert "AML.T0057" in result["frameworks"].get("mitre_atlas", [])


def test_corp_tier_finding_tagged():
    f = _finding(check_id="ai_signin_corp_tier")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "GOVERN 1.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MAP 1.1" in result["frameworks"].get("nist_ai_rmf", [])
    # S3: replaced leaked RMF subcategory format with canonical GAI-N identifiers
    assert "GAI-9" in result["frameworks"].get("nist_ai_600_1", [])
    assert "GOVERN 1.6" not in result["frameworks"].get("nist_ai_600_1", [])
    assert "Article 9" in result["frameworks"].get("eu_ai_act", [])
    # corp_tier should NOT carry Article 26 (deployer obligations for high-risk
    # systems) — only the unknown/personal tiers do
    assert "Article 26" not in result["frameworks"].get("eu_ai_act", [])


def test_unknown_tier_finding_tagged():
    f = _finding(check_id="ai_signin_unknown_tier")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "MAP 1.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MAP 5.1" in result["frameworks"].get("nist_ai_rmf", [])
    # S3: replaced leaked RMF subcategory format with canonical GAI-N identifiers
    assert "GAI-2" in result["frameworks"].get("nist_ai_600_1", [])
    assert "GAI-8" in result["frameworks"].get("nist_ai_600_1", [])
    assert "GAI-9" in result["frameworks"].get("nist_ai_600_1", [])
    assert "GOVERN 1.6" not in result["frameworks"].get("nist_ai_600_1", [])
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


# --- CME-v2 S1: end-to-end apply with both stages ---


def test_apply_runs_normalize_before_augment():
    """End-to-end: a finding with Shasta-format tags + a matching rule lands
    with canonical tags (from normalize) AND added rule tags (from augment)."""
    registry = {
        "frameworks": {
            "nist_ai_rmf": {
                "name": "x", "family": "ai", "source": "x",
                "control_descriptions": {},
                "rewrite_rules": [
                    {"from": "GOVERN-6", "to": ["GOVERN 6.1", "GOVERN 6.2"]},
                ],
            },
            "eu_ai_act": {
                "name": "x", "family": "ai", "source": "x",
                "control_descriptions": {},
                "rewrite_rules": [],
            },
        },
        "rules": [
            {
                "id": "add_eu_ai_act_to_governance_check",
                "when": {"check_id_eq": "scanner-governance-check"},
                "add_frameworks": {"eu_ai_act": ["Article 9"]},
            },
        ],
    }
    # Scanner emitted Shasta-format GOVERN-6
    f = _finding(check_id="scanner-governance-check",
                 frameworks={"nist_ai_rmf": ["GOVERN-6"]})
    fr.apply(f, entity_index={}, registry=registry)
    # Normalize ran first → GOVERN-6 expanded to canonical subcategories
    assert f["frameworks"]["nist_ai_rmf"] == ["GOVERN 6.1", "GOVERN 6.2"]
    # Augment ran second → rule added Article 9 to eu_ai_act
    assert f["frameworks"]["eu_ai_act"] == ["Article 9"]
    # Provenance recorded
    assert "add_eu_ai_act_to_governance_check" in f["evidence_packet"]["_registry_rule_ids"]


# --- CME-v2 S2: mitre_atlas rewrite table ---


def test_mitre_atlas_shasta_ids_passthrough_via_rewrite():
    """All 15 Shasta-emitted MITRE ATLAS IDs round-trip identically through normalize."""
    SHASTA_IDS = [
        "AML.T0000", "AML.T0001", "AML.T0003", "AML.T0004",
        "AML.T0010", "AML.T0011", "AML.T0012", "AML.T0015",
        "AML.T0024", "AML.T0025", "AML.T0029", "AML.T0031",
        "AML.T0035", "AML.T0051", "AML.T0052",
    ]
    f = _finding(check_id="x", frameworks={"mitre_atlas": list(SHASTA_IDS)})
    fr._normalize_stage(f, registry=fr.load_registry())
    # All inputs preserved (sorted, deduped — same set)
    assert set(f["frameworks"]["mitre_atlas"]) == set(SHASTA_IDS)


# --- CME-v2 S2: owasp_llm_top10 rewrite table ---


def test_owasp_llm_top10_year_pinning_rewrite():
    """Shasta's bare LLMNN IDs (2023 numbering) rewrite to LLMNN:2025 (current edition)."""
    # LLM01:2025 is Prompt Injection in both 2023 and 2025 — stable position, no renumbering.
    f = _finding(check_id="x", frameworks={"owasp_llm_top10": ["LLM01"]})
    fr._normalize_stage(f, registry=fr.load_registry())
    assert "LLM01:2025" in f["frameworks"]["owasp_llm_top10"]


def test_owasp_llm_top10_renumbered_items_map_to_2025_ids():
    """2023's LLM06 Sensitive Information Disclosure became 2025's LLM02:2025."""
    # Verified: 2023 LLM06 → 2025 LLM02:2025 (moved from #6 to #2 in the 2025 edition).
    f = _finding(check_id="x", frameworks={"owasp_llm_top10": ["LLM06"]})
    fr._normalize_stage(f, registry=fr.load_registry())
    assert "LLM02:2025" in f["frameworks"]["owasp_llm_top10"]


# --- CME-v2 S2: eu_ai_act rewrite table ---


def test_eu_ai_act_euai_prefix_rewrites_to_article():
    """Shasta's EUAI-N IDs rewrite to canonical Article N."""
    SHASTA_IDS = ["EUAI-9", "EUAI-10", "EUAI-11", "EUAI-12", "EUAI-13", "EUAI-14", "EUAI-15"]
    f = _finding(check_id="x", frameworks={"eu_ai_act": list(SHASTA_IDS)})
    fr._normalize_stage(f, registry=fr.load_registry())
    # Each EUAI-N becomes Article N
    for n in [9, 10, 11, 12, 13, 14, 15]:
        assert f"Article {n}" in f["frameworks"]["eu_ai_act"]
    # No EUAI- prefix should remain
    assert not any(c.startswith("EUAI-") for c in f["frameworks"]["eu_ai_act"])


def test_eu_ai_act_euai_52_resolves_to_canonical_article():
    """EUAI-52 maps to Article 50 in the final regulation (OJ L 2024/1689).

    Shasta used draft numbering 'Art. 52' for the limited-risk transparency
    obligations. In the final published text, that provision is Article 50
    ('Transparency Obligations for Providers and Deployers of Certain AI
    Systems'). Article 52 in the final regulation is 'Procedure' — the
    process for GPAI systemic-risk designation — an entirely different topic.
    Verified at: https://artificialintelligenceact.eu/article/50/
    """
    f = _finding(check_id="x", frameworks={"eu_ai_act": ["EUAI-52"]})
    fr._normalize_stage(f, registry=fr.load_registry())
    assert "Article 50" in f["frameworks"]["eu_ai_act"]
    assert "EUAI-52" not in f["frameworks"]["eu_ai_act"]


# --- CME-v2 S2: nist_ai_rmf rewrite table ---


def test_nist_ai_rmf_function_level_expands_to_subcategories():
    """GOVERN-6 (Shasta's function-level) expands to all its subcategories."""
    f = _finding(check_id="x", frameworks={"nist_ai_rmf": ["GOVERN-6"]})
    fr._normalize_stage(f, registry=fr.load_registry())
    # GOVERN 6 contains 6.1 and 6.2 per NIST AI 100-1 §5
    expanded = f["frameworks"]["nist_ai_rmf"]
    assert "GOVERN 6.1" in expanded
    assert "GOVERN 6.2" in expanded
    # No Shasta hyphen-format should remain
    assert "GOVERN-6" not in expanded


def test_nist_ai_rmf_measure_expansion():
    """MEASURE-2 expands to its subcategories (MEASURE 2.x)."""
    f = _finding(check_id="x", frameworks={"nist_ai_rmf": ["MEASURE-2"]})
    fr._normalize_stage(f, registry=fr.load_registry())
    expanded = f["frameworks"]["nist_ai_rmf"]
    # MEASURE 2 has 13 subcategories (2.1–2.13) per NIST AI 100-1 §5
    assert any(c.startswith("MEASURE 2.") for c in expanded)
    assert "MEASURE-2" not in expanded


def test_nist_ai_rmf_slice_e_subcategories_pass_through():
    """Slice E's already-canonical subcategory IDs (e.g., GOVERN 3.2) pass through unchanged."""
    f = _finding(check_id="x", frameworks={"nist_ai_rmf": ["GOVERN 3.2"]})
    fr._normalize_stage(f, registry=fr.load_registry())
    assert "GOVERN 3.2" in f["frameworks"]["nist_ai_rmf"]


# --- CME-v2 S2: nist_ai_600_1 rewrite table ---


def test_nist_ai_600_1_gai_ids_passthrough():
    """All 12 GAI-N risk identifiers from Shasta pass through unchanged."""
    SHASTA_IDS = [f"GAI-{n}" for n in range(1, 13)]
    f = _finding(check_id="x", frameworks={"nist_ai_600_1": list(SHASTA_IDS)})
    fr._normalize_stage(f, registry=fr.load_registry())
    for n in range(1, 13):
        assert f"GAI-{n}" in f["frameworks"]["nist_ai_600_1"]


def test_nist_ai_600_1_govern_id_passes_through_normalize():
    """A bare `GOVERN 1.6` value in nist_ai_600_1 survives normalize unchanged.

    S3 removed the Slice E format leak: the three ai_signin_* rules no longer
    emit `GOVERN 1.6` into nist_ai_600_1 (they now emit GAI-N identifiers).
    However, if historical data or an external source places `GOVERN 1.6` into
    the nist_ai_600_1 framework list, normalize passes it through as-is (no
    rewrite rule matches it), preserving data rather than silently dropping it.
    """
    f = _finding(check_id="x", frameworks={"nist_ai_600_1": ["GOVERN 1.6"]})
    fr._normalize_stage(f, registry=fr.load_registry())
    assert "GOVERN 1.6" in f["frameworks"]["nist_ai_600_1"]


# --- CME-v2 S3: baseline rules for AI-domain check_ids ---


def test_baseline_bedrock_content_filter():
    """bedrock-content-filter gets nist_ai_rmf + iso_42001 gap-fills."""
    f = _finding(check_id="bedrock-content-filter")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "MANAGE 2.4" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MEASURE 2.6" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MEASURE 2.7" in result["frameworks"].get("nist_ai_rmf", [])
    assert "A.6.2.4" in result["frameworks"].get("iso_42001", [])
    assert "baseline_bedrock_content_filter" in result["evidence_packet"]["_registry_rule_ids"]


def test_baseline_bedrock_vpc_endpoint():
    """bedrock-vpc-endpoint gets nist_ai_rmf + eu_ai_act + iso_42001 gap-fills."""
    f = _finding(check_id="bedrock-vpc-endpoint")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "MANAGE 2.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MEASURE 2.7" in result["frameworks"].get("nist_ai_rmf", [])
    assert "Article 15" in result["frameworks"].get("eu_ai_act", [])
    assert "A.8.2" in result["frameworks"].get("iso_42001", [])
    assert "baseline_bedrock_vpc_endpoint" in result["evidence_packet"]["_registry_rule_ids"]


def test_baseline_cloudtrail_ai_events():
    """cloudtrail-ai-events gets iso_42001 + nist_ai_600_1 + owasp_llm_top10 gap-fills."""
    f = _finding(check_id="cloudtrail-ai-events")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "A.8.2" in result["frameworks"].get("iso_42001", [])
    assert "GAI-8" in result["frameworks"].get("nist_ai_600_1", [])
    assert "LLM05:2025" in result["frameworks"].get("owasp_llm_top10", [])
    assert "baseline_cloudtrail_ai_events" in result["evidence_packet"]["_registry_rule_ids"]


def test_baseline_s3_training_data_versioned():
    """s3-training-data-versioned gets nist_ai_rmf + eu_ai_act gap-fills."""
    f = _finding(check_id="s3-training-data-versioned")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "MANAGE 4.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MEASURE 2.6" in result["frameworks"].get("nist_ai_rmf", [])
    assert "Article 10" in result["frameworks"].get("eu_ai_act", [])
    assert "baseline_s3_training_data_versioned" in result["evidence_packet"]["_registry_rule_ids"]


def test_baseline_sagemaker_notebook_root_access():
    """sagemaker-notebook-root-access gets comprehensive AI framework coverage (was soc2+fedramp only)."""
    f = _finding(check_id="sagemaker-notebook-root-access")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    # nist_ai_rmf: privileged access to AI systems + third-party/library risk via notebook
    assert "GOVERN 6.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MANAGE 2.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MANAGE 2.2" in result["frameworks"].get("nist_ai_rmf", [])
    # eu_ai_act: cybersecurity + risk management system
    assert "Article 9" in result["frameworks"].get("eu_ai_act", [])
    assert "Article 15" in result["frameworks"].get("eu_ai_act", [])
    # nist_ai_600_1: third-party/supply chain risk (GAI-9)
    assert "GAI-9" in result["frameworks"].get("nist_ai_600_1", [])
    # owasp_llm_top10: root access = excessive privilege (LLM06:2025 Excessive Agency)
    assert "LLM06:2025" in result["frameworks"].get("owasp_llm_top10", [])
    # mitre_atlas: Valid Accounts (AML.T0012) — root-access enables backdoor injection
    assert "AML.T0012" in result["frameworks"].get("mitre_atlas", [])
    assert "baseline_sagemaker_notebook_root_access" in result["evidence_packet"]["_registry_rule_ids"]


def test_baseline_sagemaker_training_vpc():
    """sagemaker-training-vpc gets full AI framework coverage (was soc2+fedramp only)."""
    f = _finding(check_id="sagemaker-training-vpc")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "MANAGE 2.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MEASURE 2.7" in result["frameworks"].get("nist_ai_rmf", [])
    assert "Article 15" in result["frameworks"].get("eu_ai_act", [])
    assert "GAI-9" in result["frameworks"].get("nist_ai_600_1", [])
    assert "LLM03:2025" in result["frameworks"].get("owasp_llm_top10", [])
    assert "AML.T0024" in result["frameworks"].get("mitre_atlas", [])
    assert "AML.T0025" in result["frameworks"].get("mitre_atlas", [])
    assert "baseline_sagemaker_training_vpc" in result["evidence_packet"]["_registry_rule_ids"]


def test_baseline_sagemaker_model_approval():
    """sagemaker-model-approval gets AI framework gap-fills for model governance."""
    f = _finding(check_id="sagemaker-model-approval")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "GOVERN 6.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MANAGE 1.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MEASURE 2.5" in result["frameworks"].get("nist_ai_rmf", [])
    assert "Article 9" in result["frameworks"].get("eu_ai_act", [])
    assert "GAI-6" in result["frameworks"].get("nist_ai_600_1", [])
    assert "GAI-9" in result["frameworks"].get("nist_ai_600_1", [])
    assert "LLM03:2025" in result["frameworks"].get("owasp_llm_top10", [])
    assert "baseline_sagemaker_model_approval" in result["evidence_packet"]["_registry_rule_ids"]


def test_baseline_sagemaker_endpoint_encryption():
    """sagemaker-endpoint-encryption gets AI framework gap-fills for data-in-transit protection."""
    f = _finding(check_id="sagemaker-endpoint-encryption")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "MANAGE 2.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MEASURE 2.7" in result["frameworks"].get("nist_ai_rmf", [])
    assert "Article 15" in result["frameworks"].get("eu_ai_act", [])
    assert "GAI-9" in result["frameworks"].get("nist_ai_600_1", [])
    assert "LLM02:2025" in result["frameworks"].get("owasp_llm_top10", [])
    assert "baseline_sagemaker_endpoint_encryption" in result["evidence_packet"]["_registry_rule_ids"]


def test_baseline_sagemaker_data_capture():
    """sagemaker-data-capture gets AI framework gap-fills for inference monitoring/audit."""
    f = _finding(check_id="sagemaker-data-capture")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "MANAGE 4.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MEASURE 2.4" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MEASURE 2.7" in result["frameworks"].get("nist_ai_rmf", [])
    assert "Article 12" in result["frameworks"].get("eu_ai_act", [])
    assert "Article 15" in result["frameworks"].get("eu_ai_act", [])
    assert "GAI-8" in result["frameworks"].get("nist_ai_600_1", [])
    assert "LLM05:2025" in result["frameworks"].get("owasp_llm_top10", [])
    assert "baseline_sagemaker_data_capture" in result["evidence_packet"]["_registry_rule_ids"]


def test_baseline_sagemaker_model_registry_access():
    """sagemaker-model-registry-access gets AI framework gap-fills for model access control."""
    f = _finding(check_id="sagemaker-model-registry-access")
    result = fr.apply(f, entity_index={}, registry=fr.load_registry())
    assert "GOVERN 6.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MANAGE 2.1" in result["frameworks"].get("nist_ai_rmf", [])
    assert "MANAGE 2.2" in result["frameworks"].get("nist_ai_rmf", [])
    assert "Article 9" in result["frameworks"].get("eu_ai_act", [])
    assert "Article 15" in result["frameworks"].get("eu_ai_act", [])
    assert "GAI-9" in result["frameworks"].get("nist_ai_600_1", [])
    assert "GAI-10" in result["frameworks"].get("nist_ai_600_1", [])
    assert "LLM06:2025" in result["frameworks"].get("owasp_llm_top10", [])
    assert "AML.T0012" in result["frameworks"].get("mitre_atlas", [])
    assert "AML.T0035" in result["frameworks"].get("mitre_atlas", [])
    assert "baseline_sagemaker_model_registry_access" in result["evidence_packet"]["_registry_rule_ids"]
