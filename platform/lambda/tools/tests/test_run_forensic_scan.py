# platform/lambda/tools/tests/test_run_forensic_scan.py
from unittest.mock import patch
from tools.run_forensic_scan import handle


@patch("tools.run_forensic_scan._schedule_callback")
def test_returns_scan_id_and_eta(mock_schedule):
    result = handle({
        "target_arn":        "arn:aws:lambda:us-east-1:111:function:prod-ai-router",
        "check_kind":        "supply_chain_active_exploit",
        "conversation_id":   "conv-abc",
    }, {"sub": "x"})
    assert "scan_id" in result
    assert result["eta_seconds"] > 0
    assert "speakable" in result
    mock_schedule.assert_called_once()
