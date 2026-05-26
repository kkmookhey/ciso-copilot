from __future__ import annotations
import json
import os
import main


def test_parse_threatfox_extracts_domain_ip_hash():
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "threatfox_recent.json")
    data = json.load(open(fixture, "r", encoding="utf-8"))
    indicators = list(main.parse_threatfox(data))

    kinds = {(i.value, i.kind, i.source) for i in indicators}
    assert ("evil.example.com",                                                   "domain", "abusech_threatfox") in kinds
    assert ("185.220.101.99",                                                     "ip",     "abusech_threatfox") in kinds
    assert ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "sha256", "abusech_threatfox") in kinds
    # confidence carried through
    by_value = {i.value: i for i in indicators}
    assert by_value["evil.example.com"].confidence == 80
    assert "Cobalt Strike" in by_value["evil.example.com"].raw["malware"]


def test_parse_threatfox_strips_port_from_ip_port_kind():
    data = {"123": [{"ioc_value": "10.0.0.1:443", "ioc_type": "ip:port",
                     "threat_type": "botnet_cc", "malware": "Emotet",
                     "confidence_level": 50, "first_seen": "2026-05-25 09:00:00 UTC", "tags": []}]}
    indicators = list(main.parse_threatfox(data))
    assert indicators[0].value == "10.0.0.1"
    assert indicators[0].kind  == "ip"
