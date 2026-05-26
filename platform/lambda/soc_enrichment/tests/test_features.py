import features


def test_off_hours_evening_utc():
    # 22:00 UTC is off-hours by the default 09-18 weekday window
    assert features._is_off_hours("2026-05-25T22:00:00Z") is True


def test_off_hours_business_hours():
    # 14:00 UTC weekday — business hours
    assert features._is_off_hours("2026-05-25T14:00:00Z") is False


def test_off_hours_weekend():
    # 14:00 Sunday — off hours regardless
    assert features._is_off_hours("2026-05-24T14:00:00Z") is True


def test_compute_features_packages_all_signals(monkeypatch):
    monkeypatch.setattr(features, "_first_time_actor_on_resource",
                        lambda tenant_id, actor, resource_arn: True)
    monkeypatch.setattr(features, "_action_rarity",
                        lambda tenant_id, action: "rare")
    monkeypatch.setattr(features, "_blast_radius_proxy",
                        lambda tenant_id, actor: 14)
    monkeypatch.setattr(features, "_ti_matches", lambda row: [])
    row = {
        "tenant_id": "t1", "actor": "user/x", "resource_arn": "sg-abc",
        "title": "AuthorizeSecurityGroupIngress", "fired_at": "2026-05-25T22:00:00Z",
    }
    f = features.compute_features(row)
    assert f == {
        "first_time_actor_on_resource": True,
        "off_hours":                    True,
        "action_rarity":                "rare",
        "blast_radius_proxy":           14,
        "ti_matches":                   [],
    }


def test_compute_features_includes_ti_matches(monkeypatch):
    monkeypatch.setattr(features, "_first_time_actor_on_resource", lambda *a, **k: False)
    monkeypatch.setattr(features, "_action_rarity",                lambda *a, **k: "common")
    monkeypatch.setattr(features, "_blast_radius_proxy",           lambda *a, **k: 0)
    monkeypatch.setattr(features, "_ti_matches", lambda row: [
        {"value": "185.220.101.12", "kind": "ip", "source": "tor",
         "confidence": None, "tags": ["tor_exit"]},
    ])
    row = {"tenant_id": "t1", "actor": "user/x", "resource_arn": "sg-abc",
           "title": "AuthorizeSecurityGroupIngress", "fired_at": "2026-05-25T14:00:00Z",
           "source_ip": "185.220.101.12"}
    f = features.compute_features(row)
    assert f["ti_matches"] == [
        {"value": "185.220.101.12", "kind": "ip", "source": "tor",
         "confidence": None, "tags": ["tor_exit"]},
    ]
