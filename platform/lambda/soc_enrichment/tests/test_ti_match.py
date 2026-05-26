"""features._ti_matches: pulls extracted IOCs from the row, looks them up,
returns a list shaped for the LLM prompt + the UI."""
from __future__ import annotations

import features


def test_ti_matches_returns_db_hits(monkeypatch):
    monkeypatch.setattr(features.ioc_extract, "extract_iocs",
                        lambda row: {"ip": ["185.220.101.12"], "domain": [], "sha256": []})
    monkeypatch.setattr(features.ti_lookup, "bulk_lookup",
                        lambda values_by_kind: {
                            "185.220.101.12": [
                                {"source": "tor",           "kind": "ip", "confidence": None, "tags": ["tor_exit"]},
                                {"source": "abusech_feodo", "kind": "ip", "confidence": 80,   "tags": ["botnet_c2"]},
                            ]
                        })
    # GreyNoise not called when DB has hits — assert by setting the api_key resolver to raise
    monkeypatch.setattr(features, "_greynoise_api_key", lambda: (_ for _ in ()).throw(AssertionError("greynoise should not be called")))
    out = features._ti_matches({"tenant_id": "t1", "source_ip": "185.220.101.12",
                                "before_state": None, "after_state": None})
    assert out == [
        {"value": "185.220.101.12", "kind": "ip", "source": "tor",
         "confidence": None, "tags": ["tor_exit"]},
        {"value": "185.220.101.12", "kind": "ip", "source": "abusech_feodo",
         "confidence": 80, "tags": ["botnet_c2"]},
    ]


def test_ti_matches_falls_back_to_greynoise_for_unmatched_ip(monkeypatch):
    monkeypatch.setattr(features.ioc_extract, "extract_iocs",
                        lambda row: {"ip": ["198.51.100.7"], "domain": [], "sha256": []})
    monkeypatch.setattr(features.ti_lookup, "bulk_lookup", lambda v: {})
    monkeypatch.setattr(features, "_greynoise_api_key", lambda: "fake-key")
    monkeypatch.setattr(features.greynoise, "lookup_ip",
                        lambda tenant_id, ip, api_key: {
                            "source": "greynoise_community", "kind": "ip", "value": ip,
                            "classification": "malicious", "confidence": 85,
                            "name": "Mirai", "link": None,
                        })
    out = features._ti_matches({"tenant_id": "t1", "source_ip": "198.51.100.7",
                                "before_state": None, "after_state": None})
    assert len(out) == 1
    assert out[0]["source"] == "greynoise_community"
    assert out[0]["confidence"] == 85


def test_ti_matches_skips_greynoise_when_no_key(monkeypatch):
    monkeypatch.setattr(features.ioc_extract, "extract_iocs",
                        lambda row: {"ip": ["198.51.100.7"], "domain": [], "sha256": []})
    monkeypatch.setattr(features.ti_lookup, "bulk_lookup", lambda v: {})
    monkeypatch.setattr(features, "_greynoise_api_key", lambda: None)
    out = features._ti_matches({"tenant_id": "t1", "source_ip": "198.51.100.7",
                                "before_state": None, "after_state": None})
    assert out == []


def test_ti_matches_returns_empty_when_no_iocs(monkeypatch):
    monkeypatch.setattr(features.ioc_extract, "extract_iocs",
                        lambda row: {"ip": [], "domain": [], "sha256": []})
    out = features._ti_matches({"tenant_id": "t1", "source_ip": None,
                                "before_state": None, "after_state": None})
    assert out == []
