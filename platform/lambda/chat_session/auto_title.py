# platform/lambda/chat_session/auto_title.py
"""Generate a short conversation title from the first turn (user + assistant).

Best-effort: every failure path returns None and the caller leaves the
conversation title untouched. Never raises.
"""
from __future__ import annotations

import os

from anthropic_call import call

TITLE_MODEL = os.environ.get("ANTHROPIC_TITLE_MODEL", "claude-haiku-4-5")
MAX_TITLE_CHARS = 60
MAX_INPUT_CHARS_PER_TURN = 800

_SYSTEM = (
    "You name chat conversations for a security analyst dashboard. "
    "Output 3 to 7 words, title case, no quotes, no trailing punctuation. "
    "Output ONLY the title — no preamble, no explanation."
)

_TEMPLATE = (
    "User asked:\n{user}\n\n"
    "Assistant replied:\n{assistant}\n\n"
    "Title:"
)

# Symmetric quote pairs: (open_char, close_char).
# Straight quotes use the same char for open and close.
# Curly/smart quotes use U+201C/U+201D (double) and U+2018/U+2019 (single).
_QUOTE_PAIRS = (
    ('"', '"'),                      # straight double
    ("'", "'"),                      # straight single
    ("“", "”"),            # curly double
    ("‘", "’"),            # curly single
)


def _sanitize(raw: str) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    for open_q, close_q in _QUOTE_PAIRS:
        if len(s) >= 2 and s.startswith(open_q) and s.endswith(close_q):
            s = s[1:-1].strip()
            break
    if not s:
        return None
    if len(s) > MAX_TITLE_CHARS:
        s = s[:MAX_TITLE_CHARS].rstrip()
    return s or None


def generate_title(user_text: str, assistant_text: str) -> str | None:
    user_text = (user_text or "")[:MAX_INPUT_CHARS_PER_TURN]
    assistant_text = (assistant_text or "")[:MAX_INPUT_CHARS_PER_TURN]
    if not user_text and not assistant_text:
        return None
    try:
        raw = call(
            system=_SYSTEM,
            user_message=_TEMPLATE.format(user=user_text, assistant=assistant_text),
            max_tokens=32,
            model=TITLE_MODEL,
            timeout=5,
        )
    except Exception as e:  # noqa: BLE001 -- best-effort
        print(f"auto_title: Haiku call failed: {e}")
        return None
    return _sanitize(raw)
