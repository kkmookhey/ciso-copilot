from __future__ import annotations
import os
import main


def test_parse_feodo_extracts_ips_skips_comments():
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "feodo_ipblocklist.txt")
    text = open(fixture, "r", encoding="utf-8").read()
    indicators = list(main.parse_feodo(text))
    values = [i.value for i in indicators]
    assert values == ["185.220.101.12", "198.51.100.7", "203.0.113.42"]
    for i in indicators:
        assert i.kind   == "ip"
        assert i.source == "abusech_feodo"
        assert i.confidence is None     # Feodo has no native confidence
        assert i.tags == ["botnet_c2"]  # synthetic tag, asserted across all rows
