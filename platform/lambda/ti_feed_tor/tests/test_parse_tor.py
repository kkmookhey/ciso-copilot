from __future__ import annotations
import os
import main


def test_parse_tor_extracts_ips_skips_blanks_and_comments():
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "tor_bulk_exit_list.txt")
    text = open(fixture, "r", encoding="utf-8").read()
    indicators = list(main.parse_tor(text))
    values = [i.value for i in indicators]
    assert values == ["185.220.101.12", "185.220.101.13", "198.51.100.7", "185.220.101.14"]
    for i in indicators:
        assert i.kind   == "ip"
        assert i.source == "tor"
        assert "tor_exit" in i.tags
        assert i.confidence is None
