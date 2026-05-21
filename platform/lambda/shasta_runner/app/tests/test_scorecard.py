# app/tests/test_scorecard.py
"""compute_scorecard maps covered control ids against each benchmark
catalog and reports coverage. It must count only ids that exist in the
catalog, dedupe across checks, and never exceed 100%."""
from coverage.scorecard import compute_scorecard


def test_counts_covered_controls_against_catalog():
    catalogs = {"cis_aws": [{"id": "1.1", "title": "a"}, {"id": "1.2", "title": "b"}]}
    coverage_map = {"check-x": {"cis_aws": ["1.1"]}}
    result = compute_scorecard(catalogs, coverage_map)
    cis = result["benchmarks"]["cis_aws"]
    assert cis["total"] == 2
    assert cis["covered"] == 1
    assert cis["coverage_pct"] == 50.0
    assert cis["uncovered"] == ["1.2"]


def test_dedupes_controls_covered_by_multiple_checks():
    catalogs = {"cis_aws": [{"id": "1.1", "title": "a"}]}
    coverage_map = {"check-x": {"cis_aws": ["1.1"]}, "check-y": {"cis_aws": ["1.1"]}}
    result = compute_scorecard(catalogs, coverage_map)
    assert result["benchmarks"]["cis_aws"]["covered"] == 1


def test_ignores_covered_ids_not_in_catalog():
    catalogs = {"cis_aws": [{"id": "1.1", "title": "a"}]}
    coverage_map = {"check-x": {"cis_aws": ["1.1", "9.9"]}}
    cis = compute_scorecard(catalogs, coverage_map)["benchmarks"]["cis_aws"]
    assert cis["covered"] == 1
    assert cis["coverage_pct"] == 100.0
