from __future__ import annotations
import json, os
import main


def test_parse_kev_extracts_cves():
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "cisa_kev.json")
    data = json.load(open(fixture, "r", encoding="utf-8"))
    indicators = list(main.parse_kev(data))
    cves = [i.value for i in indicators]
    assert cves == ["CVE-2024-12345", "CVE-2026-99999"]
    for i in indicators:
        assert i.kind   == "cve"
        assert i.source == "kev"
    by_cve = {i.value: i for i in indicators}
    # Ransomware-tagged entries get a higher confidence
    assert by_cve["CVE-2024-12345"].confidence == 95
    assert by_cve["CVE-2026-99999"].confidence == 80
    assert "ransomware" in by_cve["CVE-2024-12345"].tags
    assert by_cve["CVE-2024-12345"].raw["vendor"] == "Acme"
