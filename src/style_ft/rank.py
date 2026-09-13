"""Deterministic pseudo-random ranking.

Every sampling stage keys on the message text so a rerun picks the same
messages. That only works if the stages are independent: two stages using the
same hash namespace both prefer the same messages, so whatever survives the
first stage is exactly what the second stage over-selects. Each caller must
therefore pass its own `namespace`.
"""

from __future__ import annotations

import hashlib


def stable_rank(text: str, seed: str, namespace: str) -> float:
    """A value in [0, 1) determined by (namespace, seed, text)."""
    digest = hashlib.sha256(f"{namespace}:{seed}:{text}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64
