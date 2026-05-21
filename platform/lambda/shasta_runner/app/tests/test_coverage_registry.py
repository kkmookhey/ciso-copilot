# app/tests/test_coverage_registry.py
"""The registry aggregates all checks + collectors and filters by tier."""
from coverage.registry import ALL_CHECKS, COLLECTORS, checks_for_tier


def test_all_checks_are_unique_and_non_empty():
    ids = [c.check_id for c in ALL_CHECKS]
    assert ids, "registry has no checks"
    assert len(ids) == len(set(ids)), "duplicate check_id in registry"


def test_every_check_service_has_a_collector():
    for c in ALL_CHECKS:
        assert c.service in COLLECTORS, f"no collector for service {c.service}"


def test_quick_tier_is_a_subset_of_medium():
    quick = {c.check_id for c in checks_for_tier("quick")}
    medium = {c.check_id for c in checks_for_tier("medium")}
    deep = {c.check_id for c in checks_for_tier("deep")}
    assert quick, "no quick-tier checks"
    assert quick <= medium <= deep
    assert deep == {c.check_id for c in ALL_CHECKS}


def test_quick_tier_excludes_medium_only_checks():
    quick = {c.check_id for c in checks_for_tier("quick")}
    # sqs-dlq-configured is min_tier=medium — must not run at quick.
    assert "sqs-dlq-configured" not in quick
    assert "sqs-encryption-at-rest" in quick
