# platform/lambda/chat_session/tests/test_auto_title.py
"""Tests for auto_title.generate_title.

Every test mocks anthropic_call.call so no network or AWS access happens.
"""
import auto_title as AT


def _patch_call(monkeypatch, return_value=None, raises=None):
    """Replace auto_title.call with a stub that returns or raises."""
    calls = []

    def fake_call(*, system, user_message, max_tokens, model, timeout):
        calls.append({
            "system":       system,
            "user_message": user_message,
            "max_tokens":   max_tokens,
            "model":        model,
            "timeout":      timeout,
        })
        if raises is not None:
            raise raises
        return return_value

    monkeypatch.setattr(AT, "call", fake_call)
    return calls


def test_happy_path_returns_title(monkeypatch):
    _patch_call(monkeypatch, return_value="AWS Critical Findings Overview")
    title = AT.generate_title("show me my AWS criticals", "You have 12 critical findings…")
    assert title == "AWS Critical Findings Overview"


def test_strips_surrounding_straight_quotes(monkeypatch):
    _patch_call(monkeypatch, return_value='"AWS Critical Findings"')
    assert AT.generate_title("q", "a") == "AWS Critical Findings"


def test_strips_surrounding_smart_quotes(monkeypatch):
    _patch_call(monkeypatch, return_value="“AWS Critical Findings”")
    assert AT.generate_title("q", "a") == "AWS Critical Findings"


def test_caps_length_at_60_chars(monkeypatch):
    long = "X" * 200
    _patch_call(monkeypatch, return_value=long)
    result = AT.generate_title("q", "a")
    assert result is not None
    assert len(result) <= 60
    assert result == "X" * 60


def test_returns_none_on_exception(monkeypatch):
    _patch_call(monkeypatch, raises=RuntimeError("Anthropic HTTP 500"))
    assert AT.generate_title("q", "a") is None


def test_returns_none_on_empty_model_output(monkeypatch):
    _patch_call(monkeypatch, return_value="")
    assert AT.generate_title("q", "a") is None


def test_returns_none_on_whitespace_only_output(monkeypatch):
    _patch_call(monkeypatch, return_value="   \n  \t")
    assert AT.generate_title("q", "a") is None


def test_returns_none_when_both_inputs_empty(monkeypatch):
    calls = _patch_call(monkeypatch, return_value="never called")
    assert AT.generate_title("", "") is None
    assert calls == []  # the Haiku call must NOT be made


def test_truncates_long_inputs_before_call(monkeypatch):
    calls = _patch_call(monkeypatch, return_value="Some Title")
    user = "U" * 5000
    asst = "A" * 5000
    AT.generate_title(user, asst)
    assert len(calls) == 1
    forwarded = calls[0]["user_message"]
    # Each turn should be capped to MAX_INPUT_CHARS_PER_TURN (800)
    assert "U" * 800 in forwarded
    assert "U" * 801 not in forwarded
    assert "A" * 800 in forwarded
    assert "A" * 801 not in forwarded


def test_uses_haiku_model_and_short_timeout(monkeypatch):
    calls = _patch_call(monkeypatch, return_value="Title Here")
    AT.generate_title("q", "a")
    assert calls[0]["model"] == "claude-haiku-4-5"
    assert calls[0]["timeout"] == 5
    assert calls[0]["max_tokens"] == 32
