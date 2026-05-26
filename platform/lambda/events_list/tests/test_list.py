"""GET /events response includes AI fields."""
import json
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main


def test_list_response_includes_ai_fields(monkeypatch):
    """Each event row in the list response carries ai_narrative + ai_anomaly_class."""
    def fake_query(**kw):
        sql = kw["sql"]
        if "count(*)" in sql.lower():
            return {"records": [[{"longValue": 1}]]}
        return {"records": [[
            {"stringValue": "11111111-1111-1111-1111-111111111111"},
            {"stringValue": "drift"}, {"stringValue": "aws.config"},
            {"stringValue": "high"},  {"stringValue": "SG opened"},
            {"isNull": True},
            {"stringValue": "sg-abc"},
            {"stringValue": "user/x"},
            {"stringValue": "2026-05-25T18:42:10Z"},
            {"stringValue": "2026-05-25T18:42:12Z"},
            {"stringValue": "Suspicious change to public SG."},
            {"stringValue": "suspicious"},
            {"longValue":   88},
        ]]}
    monkeypatch.setattr(main.rds_data, "execute_statement", fake_query)
    monkeypatch.setattr(main, "_resolve_tenant_id", lambda e: "t1")

    resp = main.handler({"resource": "/events", "queryStringParameters": {}}, None)
    body = json.loads(resp["body"])
    assert body["events"][0]["ai_narrative"] == "Suspicious change to public SG."
    assert body["events"][0]["ai_anomaly_class"] == "suspicious"
    assert body["events"][0]["ai_anomaly_score"] == 88
