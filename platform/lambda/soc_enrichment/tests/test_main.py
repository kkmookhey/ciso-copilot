import main


def test_handler_processes_each_record(sample_sqs_event, sample_event_row, monkeypatch):
    """Handler loads the events row, runs the pipeline, and UPDATEs ai_* fields."""
    loads, updates = [], []

    monkeypatch.setattr(main, "_load_event_row",
                        lambda event_id, tenant_id: (loads.append((event_id, tenant_id)) or sample_event_row))
    monkeypatch.setattr(main, "compute_features",  lambda row: {"first_time_actor": True})
    monkeypatch.setattr(main, "call_llm",          lambda row, features: {
        "narrative": "Suspicious change to public SG.",
        "anomaly_class": "suspicious", "anomaly_score": 88,
        "next_steps": [{"step": "Revoke ingress", "command": "aws ec2 revoke-security-group-ingress ..."}],
        "mitre_technique": "T1098",
    })
    monkeypatch.setattr(main, "_update_event_ai", lambda **kw: updates.append(kw))

    main.handler(sample_sqs_event, None)

    assert loads == [("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222")]
    assert len(updates) == 1
    u = updates[0]
    assert u["narrative"] == "Suspicious change to public SG."
    assert u["anomaly_class"] == "suspicious"
    assert u["features"] == {"first_time_actor": True}


def test_handler_skips_missing_row(sample_sqs_event, monkeypatch):
    """If the events row vanished (TTL, race), log + return without UPDATE."""
    monkeypatch.setattr(main, "_load_event_row", lambda *_: None)
    updates = []
    monkeypatch.setattr(main, "_update_event_ai", lambda **kw: updates.append(kw))
    main.handler(sample_sqs_event, None)
    assert updates == []
