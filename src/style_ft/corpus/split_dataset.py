"""Step 3b: deterministic train/test split.

Split is by hash of the pair id, not by shuffling, so:
  - the same pair always lands in the same split, even as the dataset grows
  - adding data never silently moves an old sample from test into train

Usage:
  style-ft split --input data/processed/pairs.jsonl \
      --train data/processed/train.jsonl --test data/processed/test.jsonl --test-frac 0.2
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Iterable
from typing import Any

from ..common.jsonlio import log, read_jsonl, write_jsonl

BUCKETS = 10_000


def _bucket(key: str, seed: str) -> int:
    digest = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % BUCKETS


def split(
    rows: Iterable[dict[str, Any]], test_frac: float = 0.2, seed: str = "style-ft"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not 0.0 <= test_frac < 1.0:
        raise ValueError("test_frac must be in [0, 1)")
    cutoff = test_frac * BUCKETS
    train: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("id") or row.get("output", ""))
        (test if _bucket(key, seed) < cutoff else train).append(row)
    return train, test


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="data/processed/pairs.jsonl")
    ap.add_argument("--train", default="data/processed/train.jsonl")
    ap.add_argument("--test", default="data/processed/test.jsonl")
    ap.add_argument("--test-frac", type=float, default=0.2)
    ap.add_argument("--seed", default="style-ft")
    args = ap.parse_args(argv)

    train, test = split(read_jsonl(args.input), args.test_frac, args.seed)
    write_jsonl(args.train, train)
    write_jsonl(args.test, test)
    log(f"train={len(train)} -> {args.train}")
    log(f"test={len(test)} -> {args.test}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
