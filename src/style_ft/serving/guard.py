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
# A run of digits and phone punctuation, starting and ending on a digit. Slashes,
# colons and commas are left out on purpose, so dates, times and amounts never
# match; whether a match is a phone number is decided by its digit count below.
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{5,}\d(?!\w)")
_LOCAL = re.compile(r"\d{3}[-.\s]\d{4}")


def clean_history(history: object, turns: int = CONTEXT_TURNS) -> list[dict]:
    """The last `turns` turns as {"me", "text"}, stripped and clamped, or ValueError."""
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
    except (TypeError, ValueError):
        return default
    if not math.isfinite(t):
        return default
    low, high = TEMPERATURE_RANGE
    return min(high, max(low, t))


def pick_partner(value: object, allowed: Iterable[str], default: str) -> str:
    """Who the model is told it is texting. Only names it was trained to meet."""
    return value if isinstance(value, str) and value in set(allowed) else default


def _phone(match: re.Match[str]) -> str:
    s = match.group(0)
    digits = sum(c.isdigit() for c in s)
    return REDACTED if digits >= 10 or _LOCAL.fullmatch(s) else s


def redact(text: str) -> str:
    """Replace email addresses and phone numbers with REDACTED.

    A backstop for memorized contact details reaching a public page, not a fix
    for memorization: names and street addresses pass straight through.
    """
    return _PHONE.sub(_phone, _EMAIL.sub(REDACTED, text))
