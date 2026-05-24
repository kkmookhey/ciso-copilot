from __future__ import annotations

import json
from unittest.mock import patch


def _claims_event(sub: str = "sub-1") -> dict:
    return {"requestContext": {"authorizer": {"claims": {"sub": sub}}}}


def _stmt(rows: list[list[dict]]) -> dict:
    return {"records": rows}


def test_handler_returns_401_with_no_subject():
    from main import handler
    resp = handler({"requestContext": {}}, None)
    assert resp["statusCode"] == 401


@patch("main.rds_data")
def test_handler_returns_score_by_source_by_framework_top_people(mock_rds):
    # Six rds_data calls in order:
    #   1) tenant lookup
    #   2) score (by_status)
    #   3) by_source
    #   4) by_framework
    #   5) top_people
    mock_rds.execute_statement.side_effect = [
        # tenant lookup
        _stmt([[{"stringValue": "tenant-1"}]]),
        # score: fail=12 partial=5 pass=21
        _stmt([
            [{"stringValue": "fail"},    {"longValue": 12}],
            [{"stringValue": "partial"}, {"longValue": 5}],
            [{"stringValue": "pass"},    {"longValue": 21}],
        ]),
        # by_source: aws=7 azure=4 code=6 entra=0 (entra omitted)
        _stmt([
            [{"stringValue": "aws"},   {"longValue": 7}],
            [{"stringValue": "azure"}, {"longValue": 4}],
            [{"stringValue": "code"},  {"longValue": 6}],
        ]),
        # by_framework: nist_ai_rmf fail=4 partial=1 pass=8 ; iso_42001 fail=3 partial=2 pass=6
        _stmt([
            [{"stringValue": "nist_ai_rmf"}, {"stringValue": "fail"},    {"longValue": 4}],
            [{"stringValue": "nist_ai_rmf"}, {"stringValue": "partial"}, {"longValue": 1}],
            [{"stringValue": "nist_ai_rmf"}, {"stringValue": "pass"},    {"longValue": 8}],
            [{"stringValue": "iso_42001"},   {"stringValue": "fail"},    {"longValue": 3}],
            [{"stringValue": "iso_42001"},   {"stringValue": "partial"}, {"longValue": 2}],
            [{"stringValue": "iso_42001"},   {"stringValue": "pass"},    {"longValue": 6}],
        ]),
        # top_people
        _stmt([
            [{"stringValue": "alice@acme.com"},
             {"longValue": 3}, {"longValue": 1},
             {"stringValue": "aws,code"}],
            [{"stringValue": "bob@acme.com"},
             {"longValue": 1}, {"longValue": 2},
             {"stringValue": "code"}],
        ]),
    ]

    from main import handler
    resp = handler(_claims_event(), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["score"] == {"fail": 12, "partial": 5, "pass": 21}
    assert body["by_source"] == {"aws": 7, "azure": 4, "code": 6, "entra": 0}
    assert body["by_framework"]["nist_ai_rmf"] == {"fail": 4, "partial": 1, "pass": 8}
    assert body["by_framework"]["iso_42001"]   == {"fail": 3, "partial": 2, "pass": 6}
    assert body["by_framework"]["soc2_ai"]     == {"fail": 0, "partial": 0, "pass": 0}
    assert body["by_framework"]["eu_ai_act"]   == {"fail": 0, "partial": 0, "pass": 0}
    assert body["top_people"][0]["email"] == "alice@acme.com"
    assert body["top_people"][0]["sources"] == ["aws", "code"]
