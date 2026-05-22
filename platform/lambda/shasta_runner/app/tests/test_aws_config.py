# app/tests/test_aws_config.py
"""SCAN_BOTO_CONFIG uses adaptive retry — client-side, throttle-aware
rate limiting — so 16-way concurrent scanning does not hammer a
throttling service."""
from aws_config import SCAN_BOTO_CONFIG


def test_scan_config_uses_adaptive_retry():
    assert SCAN_BOTO_CONFIG.retries["mode"] == "adaptive"
    assert SCAN_BOTO_CONFIG.retries["max_attempts"] >= 3


def test_scan_config_keeps_timeouts():
    assert SCAN_BOTO_CONFIG.connect_timeout == 10
    assert SCAN_BOTO_CONFIG.read_timeout == 30
