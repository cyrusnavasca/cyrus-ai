"""Step 2b': choose which conversations feed the fine-tune.

Two problems this solves:

1. One thread usually dominates. If a partner or best friend accounts for half
   your outgoing messages, the model learns that relationship's register rather
   than your general style. `--max-per-thread` caps any single conversation.
2. Some threads should not be in there at all - work channels, a group whose
   dynamic is nothing like you, an ex. `--exclude` / `--include` handle that.

`--list` prints threads ranked by how many messages you wrote in them, which is
the selection menu for the flags above.

Usage:
  python -m style_ft.select_threads --input data/interim/messages.jsonl --list
  python -m style_ft.select_threads --input data/interim/messages.jsonl \
      --output data/interim/selected.jsonl --max-per-thread 8000 --exclude "work chat"
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path
from typing import Any, Iterable

from .jsonlio import log, read_jsonl, write_jsonl
from .rank import stable_rank


def thread_summary(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    mine: collections.Counter = collections.Counter()
    total: collections.Counter = collections.Counter()
    is_group: dict[str, bool] = {}
    span: dict[str, tuple[str, str]] = {}
    for row in rows:
        thread = row.get("thread_id") or "(none)"
        total[thread] += 1
        if row.get("direction") == "out":
            mine[thread] += 1
        is_group[thread] = bool(row.get("is_group"))
        day = (row.get("timestamp") or "")[:10]
        if day:
            first, last = span.get(thread, (day, day))
            span[thread] = (min(first, day), max(last, day))
    return [
        {
            "thread_id": thread,
            "mine": count,
            "total": total[thread],
            "kind": "group" if is_group.get(thread) else "dm",
            "first": span.get(thread, ("", ""))[0],
            "last": span.get(thread, ("", ""))[1],
        }
        for thread, count in mine.most_common()
    ]


def _matches(thread: str, patterns: list[str]) -> bool:
    """Case-insensitive substring match; a thread id matches exactly too."""
    lowered = thread.lower()
    return any(p.lower() in lowered for p in patterns)


def select(
    rows: Iterable[dict[str, Any]],
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    kind: str = "all",
    max_per_thread: int | None = None,
    min_per_thread: int = 0,
    outgoing_only: bool = False,
    seed: str = "style-ft",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = list(rows)
    include = include or []
    exclude = exclude or []

    by_thread: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    stats = {"input": len(rows), "excluded_thread": 0, "wrong_kind": 0,
             "not_included": 0, "below_min": 0, "capped": 0}

    for row in rows:
        thread = row.get("thread_id") or "(none)"
        if exclude and _matches(thread, exclude):
            stats["excluded_thread"] += 1
            continue
        if include and not _matches(thread, include):
            stats["not_included"] += 1
            continue
        if kind == "dm" and row.get("is_group"):
            stats["wrong_kind"] += 1
            continue
        if kind == "group" and not row.get("is_group"):
            stats["wrong_kind"] += 1
            continue
        if outgoing_only and row.get("direction") != "out":
            continue
        by_thread[thread].append(row)

    kept: list[dict[str, Any]] = []
    per_thread: dict[str, int] = {}
    for thread, msgs in by_thread.items():
        mine_count = sum(1 for m in msgs if m.get("direction") == "out")
        if mine_count < min_per_thread:
            stats["below_min"] += len(msgs)
            continue
        if max_per_thread is not None and mine_count > max_per_thread:
            # Cap only my messages; keep a deterministic random subset of them.
            outgoing = sorted(
                (m for m in msgs if m.get("direction") == "out"),
                key=lambda m: stable_rank(m["text"], seed, "select_threads"),
            )[:max_per_thread]
            incoming = [m for m in msgs if m.get("direction") != "out"]
            stats["capped"] += mine_count - max_per_thread
            msgs = sorted(outgoing + incoming, key=lambda m: m.get("timestamp", ""))
        kept.extend(msgs)
        per_thread[thread] = sum(1 for m in msgs if m.get("direction") == "out")

    kept.sort(key=lambda m: m.get("timestamp", ""))
    stats["threads"] = len(per_thread)
    stats["kept"] = len(kept)
    stats["kept_mine"] = sum(per_thread.values())
    stats["top_threads"] = dict(collections.Counter(per_thread).most_common(10))
    return kept, stats


def cmd_list(rows: list[dict[str, Any]], limit: int, kind: str) -> None:
    summary = thread_summary(rows)
    if kind != "all":
        summary = [s for s in summary if s["kind"] == kind]
    total_mine = sum(s["mine"] for s in summary)
    print(f"{len(summary)} threads, {total_mine} messages of mine\n")
    print(f"{'my msgs':>8} {'share':>6} {'total':>8}  {'kind':<6} {'active':<24} thread")
    running = 0
    for s in summary[:limit]:
        running += s["mine"]
        share = 100 * s["mine"] / total_mine if total_mine else 0
        print(
            f"{s['mine']:>8} {share:>5.1f}% {s['total']:>8}  {s['kind']:<6} "
            f"{s['first']}..{s['last']}  {s['thread_id'][:44]}"
        )
    if total_mine:
        print(f"\nshown: {running} ({100 * running / total_mine:.0f}% of my messages)")
        for k in (1, 5, 10, 25, 50):
            if k <= len(summary):
                cum = sum(s["mine"] for s in summary[:k])
                print(f"  top {k:>2} threads: {100 * cum / total_mine:.0f}%")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="data/interim/messages.jsonl")
    ap.add_argument("--output", default="data/interim/selected.jsonl")
    ap.add_argument("--list", action="store_true", help="print threads ranked by my message count and exit")
    ap.add_argument("--list-limit", type=int, default=40)
    ap.add_argument("--include", action="append", default=[],
                    help="only these threads (substring or exact id, repeatable)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="drop these threads (substring or exact id, repeatable)")
    ap.add_argument("--kind", default="all", choices=["all", "dm", "group"])
    ap.add_argument("--max-per-thread", type=int, default=None,
                    help="cap on my messages from any one thread")
    ap.add_argument("--min-per-thread", type=int, default=0,
                    help="skip threads where I wrote fewer than this many")
    ap.add_argument("--seed", default="style-ft")
    args = ap.parse_args(argv)

    rows = list(read_jsonl(args.input))
    if args.list:
        cmd_list(rows, args.list_limit, args.kind)
        return 0

    kept, stats = select(
        rows,
        include=args.include,
        exclude=args.exclude,
        kind=args.kind,
        max_per_thread=args.max_per_thread,
        min_per_thread=args.min_per_thread,
        seed=args.seed,
    )
    write_jsonl(args.output, kept)
    log(f"{stats['kept']} messages from {stats['threads']} threads -> {args.output}")
    log(f"mine: {stats['kept_mine']}   capped away: {stats['capped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
