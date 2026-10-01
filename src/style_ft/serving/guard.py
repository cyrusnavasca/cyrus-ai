"""Request and response guards for the public chat endpoint.

The public page reaches the model through a Vercel proxy that applies the same
limits, but anyone who finds the Modal URL can call it directly, so nothing here
trusts the proxy. Stdlib only: deploy/serve.py imports this inside the Modal
image, and the tests import it on a laptop.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

# Must match CONTEXT_TURNS in deploy/serve.py: the pairs were built with this
# window, and more turns than the model was trained on degrades it.
CONTEXT_TURNS = 6
MAX_CHARS = 300
# Checked on the raw turns, before clamping. 6 x 300 always fits, so a check
# after clamping could never fire; this one rejects a payload that is too big
# rather than silently cutting it down.
MAX_TOTAL_CHARS = 2000
TEMPERATURE_DEFAULT = 0.5
TEMPERATURE_RANGE = (0.3, 0.8)

REDACTED = "[redacted]"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Explicit phone shapes rather than "enough digits": a texting corpus is full of
# dates, times, scores and ranges, and a digit-count rule eats "2026-10-01 12:30".
_PHONES = [
    # North American: optional +1, area code (optionally in parens), 3, 4.
    re.compile(r"(?<![\w+])(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)"),
    # International with a leading +: country code, then 2-5 groups of 2-4 digits.
    re.compile(r"(?<![\w+])\+[1-9]\d{0,2}(?:[\s.-]?\d{2,4}){2,5}(?!\w)"),
    # Seven-digit local number, dash or dot only ("555-0123").
    re.compile(r"(?<![\w+.-])\d{3}[-.]\d{4}(?![\w]|[-.]\d)"),
]


def clean_history(history: object, turns: int = CONTEXT_TURNS) -> list[dict]:
    """The last `turns` turns as {"me", "text"}, stripped and clamped, or ValueError."""
    if turns < 1:
        raise ValueError("turns must be at least 1")
    if not isinstance(history, list) or not history:
        raise ValueError("history must be a non-empty list")
    recent = history[-turns:]
    out = []
    raw_total = 0
    for item in recent:
        if not isinstance(item, dict):
            raise ValueError("each turn must be an object")
        text = item.get("text", "")
        if not isinstance(text, str):
            raise ValueError("turn text must be a string")
        text = text.strip()
        raw_total += len(text)
        out.append({"me": bool(item.get("me")), "text": text[:MAX_CHARS]})
    if raw_total > MAX_TOTAL_CHARS:
        raise ValueError(f"history is over {MAX_TOTAL_CHARS} characters")
    if not out[-1]["text"]:
        raise ValueError("the newest message is empty")
    return out


def clamp_temperature(value: object, default: float = TEMPERATURE_DEFAULT) -> float:
    """A sampling temperature inside TEMPERATURE_RANGE; junk falls back to the default."""
    if value is None or value == "":
        return default
    try:
        t = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(t):
        return default
    low, high = TEMPERATURE_RANGE
    return min(high, max(low, t))


def pick_partner(value: object, allowed: Iterable[str], default: str) -> str:
    """Who the model is told it is texting. Only names it was trained to meet."""
    return value if isinstance(value, str) and value in set(allowed) else default


def redact(text: str) -> str:
    """Replace email addresses and phone numbers with REDACTED.

    A backstop for memorized contact details reaching a public page, not a fix
    for memorization: names and street addresses pass straight through.
    """
    text = _EMAIL.sub(REDACTED, text)
    for pattern in _PHONES:
        text = pattern.sub(REDACTED, text)
    return text
