"""Step 2c: pick the training subset from the filtered candidates.

Two reasons not to use every message:
  - Cost. Pair generation is one LLM call per message.
  - Recency. Writing style drifts; a corpus dominated by one old year teaches
    the style of that year.

Sampling is stratified by year, with an optional recency weight, and is
deterministic for a given seed so a rerun picks the same messages.

Also drops messages matching sensitive patterns (verification codes, long digit
runs, emails, street addresses) before anything is sent to an API.

Usage:
  python -m style_ft.sample_messages --input data/processed/my_messages.jsonl \
      --output data/processed/sampled.jsonl --n 5000 --since 2024-01-01
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from .jsonlio import log, read_jsonl, write_jsonl

# Conservative: a false positive costs one message out of tens of thousands.
SENSITIVE_PATTERNS: dict[str, str] = {
    "verification_code": r"\b(code|otp|verification|passcode)\b.{0,20}\b\d{4,8}\b",
    "long_digit_run": r"\b\d{9,}\b",
    "card_like": r"\b(?:\d[ -]*?){13,16}\b",
    "ssn_like": r"\b\d{3}-\d{2}-\d{4}\b",
    "email": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "street_address": r"\b\d{1,5}\s+\w+\s+(st|street|ave|avenue|rd|road|blvd|dr|drive|ln|lane|way|ct)\b",
    "credential": r"\b(password|passwd|pin|login)\b.{0,25}[:=]\s*\S+",
    "ip_address": r"\b\d{1,3}(\.\d{1,3}){3}\b",
}
_COMPILED = {k: re.compile(v, re.IGNORECASE) for k, v in SENSITIVE_PATTERNS.items()}


def sensitive_hits(text: str) -> list[str]:
    return [name for name, pat in _COMPILED.items() if pat.search(text)]


def _stable_rank(text: str, seed: str) -> float:
    """Deterministic pseudo-random score in [0, 1) keyed by message content."""
    digest = hashlib.sha256(f"{seed}:{text}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def sample(
    rows: Iterable[dict[str, Any]],
    n: int,
    *,
    since: str | None = None,
    recency_weight: float = 1.0,
    drop_sensitive: bool = True,
    seed: str = "style-ft",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Stratified year sample.

    recency_weight: 1.0 = proportional to how much each year contributes
    (i.e. a plain random sample), higher = bias toward recent years. The weight
    for year i (oldest = 0) is recency_weight ** i.
    """
    stats: dict[str, Any] = {
        "input": 0, "dropped_before_date": 0, "dropped_sensitive": 0,
        "sensitive_by_pattern": collections.Counter(), "eligible": 0,
    }
    by_year: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)

    for row in rows:
        stats["input"] += 1
        timestamp = row.get("timestamp", "")
        if since and timestamp[:10] < since:
            stats["dropped_before_date"] += 1
            continue
        if drop_sensitive:
            hits = sensitive_hits(row.get("text", ""))
            if hits:
                stats["dropped_sensitive"] += 1
                stats["sensitive_by_pattern"].update(hits)
                continue
        by_year[timestamp[:4] or "unknown"].append(row)
        stats["eligible"] += 1

    years = sorted(by_year)
    if not years:
        return [], stats
    if n >= stats["eligible"]:
        picked = [r for y in years for r in by_year[y]]
        stats["per_year"] = {y: len(by_year[y]) for y in years}
        stats["sampled"] = len(picked)
        stats["sensitive_by_pattern"] = dict(stats["sensitive_by_pattern"])
        return picked, stats

    # Quota per year: available count scaled by the recency weight, normalized.
    weights = {y: len(by_year[y]) * (recency_weight**i) for i, y in enumerate(years)}
    total_weight = sum(weights.values())
    quotas = {y: int(round(n * weights[y] / total_weight)) for y in years}

    # Clamp to what each year actually has, then redistribute the shortfall to
    # the most recent years that still have headroom.
    for y in years:
        quotas[y] = min(quotas[y], len(by_year[y]))
    shortfall = n - sum(quotas.values())
    for y in reversed(years):
        if shortfall <= 0:
            break
        headroom = len(by_year[y]) - quotas[y]
        take = min(headroom, shortfall)
        quotas[y] += take
        shortfall -= take

    picked: list[dict[str, Any]] = []
    for y in years:
        ranked = sorted(by_year[y], key=lambda r: _stable_rank(r["text"], seed))
        picked.extend(ranked[: quotas[y]])
    picked.sort(key=lambda r: r.get("timestamp", ""))

    stats["per_year"] = {y: quotas[y] for y in years}
    stats["available_per_year"] = {y: len(by_year[y]) for y in years}
    stats["sampled"] = len(picked)
    stats["sensitive_by_pattern"] = dict(stats["sensitive_by_pattern"])
    return picked, stats


def estimate_cost(n: int, batch: bool = True) -> dict[str, float]:
    """Rough pair-generation cost for Claude Opus 5. Prices per million tokens."""
    in_per_msg, out_per_msg = 330, 60  # measured shape of this prompt + a short rewrite
    in_price, out_price = 5.00, 25.00
    if batch:
        in_price, out_price = in_price / 2, out_price / 2
    return {
        "input_tokens": n * in_per_msg,
        "output_tokens": n * out_per_msg,
        "usd": round(n * in_per_msg / 1e6 * in_price + n * out_per_msg / 1e6 * out_price, 2),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="data/processed/my_messages.jsonl")
    ap.add_argument("--output", default="data/processed/sampled.jsonl")
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--since", default=None, help="YYYY-MM-DD, inclusive")
    ap.add_argument(
        "--recency-weight",
        type=float,
        default=1.0,
        help="per-year multiplier, oldest to newest. 1.0 = plain random sample, 1.5 = favor recent",
    )
    ap.add_argument("--keep-sensitive", action="store_true", help="do not drop pattern matches")
    ap.add_argument("--seed", default="style-ft")
    ap.add_argument("--stats-out", default=None)
    args = ap.parse_args(argv)

    picked, stats = sample(
        read_jsonl(args.input),
        args.n,
        since=args.since,
        recency_weight=args.recency_weight,
        drop_sensitive=not args.keep_sensitive,
        seed=args.seed,
    )
    write_jsonl(args.output, picked)
    cost = estimate_cost(len(picked))
    log(f"sampled {len(picked)} -> {args.output}")
    log(json.dumps(stats, indent=2))
    log(f"pair-generation estimate (Batches API): ~${cost['usd']}")
    if args.stats_out:
        Path(args.stats_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.stats_out).write_text(
            json.dumps({**stats, "cost_estimate": cost}, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
