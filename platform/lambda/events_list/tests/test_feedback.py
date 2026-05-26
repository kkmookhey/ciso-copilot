import json
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main


def test_feedback_writes_to_feedback_table(monkeypatch):
    monkeypatch.setattr(main, "_resolve_tenant_id", lambda e: "t1")
    monkeypatch.setattr(main, "_resolve_user_id",  lambda e: "u1")

    calls = []
    def fake_exec(**kw):
        calls.append(kw["sql"])
        return {"records": []}
    monkeypatch.setattr(main.rds_data, "execute_statement", fake_exec)

    resp = main.handler({
        "resource": "/events/{event_id}/feedback",
        "httpMethod": "POST",
        "pathParameters": {"event_id": "11111111-1111-1111-1111-111111111111"},
        "body": json.dumps({"sentiment": "up", "reason": "useful narrative"}),
    }, None)

    assert resp["statusCode"] == 200
    assert any("INSERT INTO feedback" in s for s in calls)
