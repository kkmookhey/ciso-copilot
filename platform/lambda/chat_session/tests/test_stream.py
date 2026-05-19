# platform/lambda/chat_session/tests/test_stream.py
import messages_stream as MS


def test_verify_jwt_rejects_missing_header():
    evt = {"headers": {}}
    assert MS._extract_bearer(evt) is None


def test_extract_bearer_parses_header():
    evt = {"headers": {"authorization": "Bearer abc.def.ghi"}}
    assert MS._extract_bearer(evt) == "abc.def.ghi"
