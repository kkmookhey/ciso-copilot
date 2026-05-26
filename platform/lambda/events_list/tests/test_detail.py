import json
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main


def test_detail_returns_full_row_plus_related_findings(monkeypatch):
    monkeypatch.setattr(main, "_resolve_tenant_id", lambda e: "t1")

    def fake_query(**kw):
        sql = kw["sql"]
        if "FROM events e" in sql:
            return {"records": [[
                {"stringValue": "11111111-1111-1111-1111-111111111111"},
                {"stringValue": "drift"}, {"stringValue": "aws.config"},
                {"stringValue": "high"},  {"stringValue": "SG opened"},
                {"isNull": True},  {"stringValue": "sg-abc"}, {"stringValue": "user/x"},
                {"stringValue": "2026-05-25T18:42:10Z"}, {"stringValue": "2026-05-25T18:42:12Z"},
                {"stringValue": "n"}, {"stringValue": "suspicious"}, {"longValue": 88},
                {"stringValue": '[{"step":"x","command":"y"}]'},
                {"stringValue": '{"off_hours":true}'},
                {"stringValue": "claude-sonnet-4-6"},
                {"stringValue": "T1098"},
                {"stringValue": "AuthorizeSecurityGroupIngress"},
                {"stringValue": '{"ipPermissions":[]}'},
                {"isNull": True},
            ]]}
        if "FROM findings" in sql:
            return {"records": [[
                {"stringValue": "ec2-22-open-world"},
                {"stringValue": "Security group open to world on SSH"},
                {"stringValue": "high"},
            ]]}
        return {"records": []}

    monkeypatch.setattr(main.rds_data, "execute_statement", fake_query)

    resp = main.handler({
        "resource": "/events/{event_id}",
        "pathParameters": {"event_id": "11111111-1111-1111-1111-111111111111"},
    }, None)
    body = json.loads(resp["body"])
    assert body["event"]["ai_narrative"] == "n"
    assert body["event"]["ai_next_steps"] == [{"step": "x", "command": "y"}]
    assert body["event"]["action"] == "AuthorizeSecurityGroupIngress"
    assert len(body["related_findings"]) == 1
    assert body["related_findings"][0]["check_id"] == "ec2-22-open-world"
