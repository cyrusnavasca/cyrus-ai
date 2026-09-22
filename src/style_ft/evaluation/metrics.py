"""Cheap, dependency-free style statistics.

These do not measure "is it good" - they measure whether the fine-tune moved
the obvious surface markers toward the reference. Useful as a fast regression
signal between training runs; real judgement still comes from eyeballing the
side-by-side report.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections.abc import Iterable
from typing import Any

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)
ABBREVS = {
    "u", "ur", "rn", "idk", "lol", "lmao", "omw", "tmrw", "tn", "thx", "ty",
    "pls", "plz", "bc", "cuz", "imo", "btw", "lmk", "nvm", "sry", "def", "prob",
    "gonna", "wanna", "kinda", "ofc", "bet", "fr", "ngl", "tbh",
}
WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def _letters(text: str) -> list[str]:
    return [c for c in text if c.isalpha()]


def message_features(text: str) -> dict[str, float]:
    text = unicodedata.normalize("NFKC", text)
    words = WORD_RE.findall(text.lower())
    letters = _letters(text)
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    return {
        "chars": float(len(text)),
        "words": float(len(words)),
        "lowercase_ratio": (sum(1 for c in letters if c.islower()) / len(letters)) if letters else 0.0,
        "emoji_per_100w": (len(EMOJI_RE.findall(text)) / len(words) * 100) if words else 0.0,
        "abbrev_ratio": (sum(1 for w in words if w in ABBREVS) / len(words)) if words else 0.0,
        "ellipsis_per_100w": (text.count("...") / len(words) * 100) if words else 0.0,
        "exclam_per_100w": (text.count("!") / len(words) * 100) if words else 0.0,
        "terminal_punct_ratio": (
            sum(1 for s in sentences if s.strip() and text.strip().endswith((".", "!", "?"))) / len(sentences)
        ) if sentences else 0.0,
        "avg_word_len": statistics.fmean(len(w) for w in words) if words else 0.0,
    }


def corpus_features(texts: Iterable[str]) -> dict[str, float]:
    rows = [message_features(t) for t in texts if t and t.strip()]
    if not rows:
        return {}
    return {k: statistics.fmean(r[k] for r in rows) for k in rows[0]}


def compare(reference: Iterable[str], base: Iterable[str], tuned: Iterable[str]) -> dict[str, Any]:
    """Per-feature reference/base/tuned means, plus which side is closer."""
    ref, bse, tnd = corpus_features(reference), corpus_features(base), corpus_features(tuned)
    out: dict[str, Any] = {}
    for key in ref:
        b_gap = abs(bse.get(key, 0.0) - ref[key])
        t_gap = abs(tnd.get(key, 0.0) - ref[key])
        out[key] = {
            "reference": round(ref[key], 3),
            "base": round(bse.get(key, 0.0), 3),
            "tuned": round(tnd.get(key, 0.0), 3),
            "closer": "tuned" if t_gap < b_gap else ("base" if b_gap < t_gap else "tie"),
        }
    return out
