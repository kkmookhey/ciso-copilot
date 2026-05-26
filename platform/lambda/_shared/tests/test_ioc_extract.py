from __future__ import annotations

import ioc_extract


def test_extract_extracts_source_ip_from_row():
    row = {"source_ip": "185.220.101.12", "before_state": None, "after_state": None}
    iocs = ioc_extract.extract_iocs(row)
    assert "185.220.101.12" in iocs["ip"]


def test_extract_extracts_ipv4_from_sg_ingress_after_state():
    row = {
        "source_ip": None,
        "before_state": None,
        "after_state": {
            "ipPermissions": {"items": [{
                "fromPort": 22, "toPort": 22,
                "ipRanges": {"items": [{"cidrIp": "203.0.113.5/32"}]},
            }]},
        },
    }
    iocs = ioc_extract.extract_iocs(row)
    assert "203.0.113.5" in iocs["ip"]
    # CIDR-to-world is a special placeholder we never want to look up
    row["after_state"]["ipPermissions"]["items"][0]["ipRanges"]["items"][0]["cidrIp"] = "0.0.0.0/0"
    iocs2 = ioc_extract.extract_iocs(row)
    assert "0.0.0.0" not in iocs2["ip"]


def test_extract_extracts_domain_and_url_strings():
    row = {"source_ip": None, "after_state": {"endpoint": "https://evil.example.com/x", "host": "another.example.org"}}
    iocs = ioc_extract.extract_iocs(row)
    assert "evil.example.com"   in iocs["domain"]
    assert "another.example.org" in iocs["domain"]


def test_extract_extracts_sha256():
    row = {"source_ip": None, "after_state": {"sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}}
    iocs = ioc_extract.extract_iocs(row)
    assert "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" in iocs["sha256"]


def test_extract_dedupes_across_keys():
    row = {
        "source_ip": "185.220.101.12",
        "after_state": {"caller_ip": "185.220.101.12", "extra": "185.220.101.12"},
    }
    iocs = ioc_extract.extract_iocs(row)
    assert iocs["ip"].count("185.220.101.12") == 1


def test_extract_returns_empty_dict_for_dry_row():
    iocs = ioc_extract.extract_iocs({"source_ip": None, "after_state": None, "before_state": None})
    assert iocs == {"ip": [], "domain": [], "sha256": []}


def test_extract_filters_cgnat_100_64_range():
    """100.64.0.0/10 is RFC 6598 CGNAT — reserved, not worth a TI lookup."""
    row = {"source_ip": "100.64.1.1", "before_state": None, "after_state": None}
    iocs = ioc_extract.extract_iocs(row)
    assert "100.64.1.1" not in iocs["ip"]
