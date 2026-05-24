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
