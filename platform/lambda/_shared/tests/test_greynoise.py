from __future__ import annotations

import json
from unittest.mock import patch

import greynoise


def test_lookup_returns_indicator_on_classification_malicious():
    fake_body = json.dumps({
        "ip": "185.220.101.12",
        "noise": True, "riot": False,
        "classification": "malicious",
        "name": "Mirai",
        "link": "https://viz.greynoise.io/ip/185.220.101.12",
        "last_seen": "2026-05-25",
        "message": "Success",
    }).encode()
    with patch.object(greynoise, "_http_get", return_value=(200, fake_body)) as m, \
         patch.object(greynoise, "_under_cap",     return_value=True), \
         patch.object(greynoise, "_increment_count"):
        ind = greynoise.lookup_ip("tenant-1", "185.220.101.12", api_key="fake")
        assert m.called
        assert ind is not None
        assert ind["source"]     == "greynoise_community"
        assert ind["classification"] == "malicious"
        assert ind["confidence"]  == 85


def test_lookup_returns_none_when_cap_reached():
    with patch.object(greynoise, "_under_cap", return_value=False) as cap, \
         patch.object(greynoise, "_http_get") as get:
        ind = greynoise.lookup_ip("tenant-1", "185.220.101.12", api_key="fake")
        assert ind is None
        cap.assert_called_once()
        get.assert_not_called()


def test_lookup_returns_none_on_404():
    with patch.object(greynoise, "_under_cap", return_value=True), \
         patch.object(greynoise, "_increment_count"), \
         patch.object(greynoise, "_http_get", return_value=(404, b'{"message":"IP not observed"}')):
        ind = greynoise.lookup_ip("tenant-1", "8.8.8.8", api_key="fake")
        assert ind is None


def test_lookup_returns_none_without_api_key():
    ind = greynoise.lookup_ip("tenant-1", "185.220.101.12", api_key=None)
    assert ind is None


def test_lookup_classifies_benign_as_low_confidence():
    fake_body = json.dumps({"ip": "8.8.8.8", "classification": "benign", "noise": True}).encode()
    with patch.object(greynoise, "_under_cap",  return_value=True), \
         patch.object(greynoise, "_increment_count"), \
         patch.object(greynoise, "_http_get", return_value=(200, fake_body)):
        ind = greynoise.lookup_ip("tenant-1", "8.8.8.8", api_key="fake")
        assert ind is not None
        assert ind["classification"] == "benign"
        assert ind["confidence"]     == 20
