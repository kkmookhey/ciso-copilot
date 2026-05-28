# platform/lambda/tools/tests/test_tail_lambda_logs.py
from unittest.mock import patch, MagicMock
from tools.tail_lambda_logs import handle


@patch("tools.tail_lambda_logs.time.sleep")
@patch("tools.tail_lambda_logs._logs")
def test_returns_matches(mock_logs, mock_sleep):
    mock_logs.start_query.return_value = {"queryId": "q-123"}
    mock_logs.get_query_results.return_value = {
        "status": "Complete",
        "results": [
            [{"field": "@timestamp", "value": "2026-05-27 12:00:00"},
             {"field": "@message",   "value": "EVENT: exec_payload received"}],
        ],
    }
    result = handle({
        "function_name":    "prod-ai-router",
        "regex":            "exec_payload",
        "window_hours":     72,
    }, {"sub": "x"})
    assert "matches" in result
    assert len(result["matches"]) == 1
    assert "speakable" in result


@patch("tools.tail_lambda_logs.time.sleep")
@patch("tools.tail_lambda_logs._logs")
def test_no_matches(mock_logs, mock_sleep):
    mock_logs.start_query.return_value = {"queryId": "q-456"}
    mock_logs.get_query_results.return_value = {"status": "Complete", "results": []}
    result = handle({
        "function_name":    "prod-ai-router",
        "regex":            "exec_payload",
        "window_hours":     72,
    }, {"sub": "x"})
    assert result["matches"] == []
    assert "no matches" in result["speakable"].lower() or "nothing" in result["speakable"].lower()
