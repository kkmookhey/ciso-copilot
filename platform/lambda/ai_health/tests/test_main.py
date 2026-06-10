import json

from ..main import handler


def test_handler_returns_ok():
    response = handler({}, None)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body == {"ok": True, "stack": "CisoCopilotAi"}


def test_handler_returns_cors_headers():
    response = handler({}, None)
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"
    assert response["headers"]["Content-Type"] == "application/json"
