import json
import llm


def test_build_prompt_includes_event_and_features():
    row = {"source": "aws.config", "kind": "drift", "severity": "high",
           "title": "AuthorizeSecurityGroupIngress",
           "actor": "arn:aws:iam::1:user/x",
           "resource_arn": "arn:aws:ec2:us-east-1:1:security-group/sg-abc",
           "fired_at": "2026-05-25T18:42:10Z",
           "after_state": {"ipPermissions": [{"fromPort": 22, "toPort": 22,
                                              "ipRanges": [{"cidrIp": "0.0.0.0/0"}]}]}}
    features = {"first_time_actor_on_resource": True, "off_hours": True,
                "action_rarity": "rare", "blast_radius_proxy": 14}
    msgs = llm.build_messages(row, features)

    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    content = msgs[1]["content"]
    assert "AuthorizeSecurityGroupIngress" in content
    assert "first_time_actor_on_resource" in content
    assert "0.0.0.0/0" in content
    assert "respond with json" in msgs[0]["content"].lower()


def test_call_llm_short_circuits_when_cap_reached(monkeypatch):
    monkeypatch.setattr(llm.spend_cap, "llm_spend_today_cents", lambda t: 9999)
    monkeypatch.setattr(llm, "DAILY_CAP_CENTS_DEFAULT", 1000)
    out = llm.call_llm({"tenant_id": "t1"}, {})
    assert out["model_version"] == "cap_reached"
    assert out["narrative"] is None


def test_call_llm_returns_parsed_response(monkeypatch):
    monkeypatch.setattr(llm.spend_cap, "llm_spend_today_cents", lambda t: 0)
    monkeypatch.setattr(llm.spend_cap, "llm_spend_add",         lambda t, c: 0)

    class FakeResp:
        def __init__(self):
            self.choices = [type("C", (), {"message": type("M", (), {
                "content": json.dumps({"narrative": "n", "anomaly_class": "unusual",
                                       "anomaly_score": 60, "next_steps": [],
                                       "mitre_technique": "T1098"})})})]
            self.usage = type("U", (), {"prompt_tokens": 100, "completion_tokens": 50})

    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: FakeResp())
    row = {"tenant_id": "t1", "source": "aws.config", "kind": "drift",
           "severity": "high", "title": "x", "actor": "u", "resource_arn": "r",
           "fired_at": "2026-05-25T00:00:00Z", "after_state": {}}
    out = llm.call_llm(row, {})
    assert out["narrative"] == "n"
    assert out["anomaly_class"] == "unusual"
    assert out["mitre_technique"] == "T1098"
