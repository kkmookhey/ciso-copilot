# platform/lambda/voice_session/tests/test_system_prompt.py
from voice_session.system_prompt import render, SHASTA_PROMPT


def test_prompt_has_persona_block():
    assert "You are Shasta" in SHASTA_PROMPT
    assert "treat him as a peer" in SHASTA_PROMPT.lower()


def test_prompt_has_never_block():
    # The throat-clearing prohibitions are load-bearing.
    assert "Great question" in SHASTA_PROMPT
    assert "I'd be happy to help" in SHASTA_PROMPT
    assert "Certainly" in SHASTA_PROMPT


def test_prompt_has_long_identifier_rule():
    # Backup guardrail for the speakable layer.
    assert "ARNs, GUIDs" in SHASTA_PROMPT
    assert "speakable" in SHASTA_PROMPT


def test_render_substitutes_first_name():
    p = render(first_name="KK", clouds=["aws (KK-test)"])
    assert "KK" in p
    assert "aws (KK-test)" in p


def test_render_handles_empty_clouds():
    p = render(first_name="KK", clouds=[])
    # Should still render with the empty-clouds fallback.
    assert "KK" in p
    assert "none connected yet" in p


def test_prompt_stays_under_4000_chars():
    # Realtime models start losing rule fidelity past ~4K char prompts.
    p = render(first_name="KK", clouds=["aws", "azure"])
    assert len(p) < 4000, f"prompt is {len(p)} chars — trim it"
