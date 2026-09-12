"""Step 3: turn real messages into (input, output) training pairs.

For each real message (the OUTPUT), generate a style-free synthetic INPUT with
an LLM. Writes JSONL: {"id", "input", "output", "input_style", "meta"}.

Resumable: rerunning appends only ids that are not already in --output, so an
interrupted long run costs nothing to restart.

Usage:
  # offline smoke test, no API key needed
  python -m style_ft.generate_pairs --input data/dummy/my_messages.jsonl \
      --output data/dummy/pairs.jsonl --provider dummy

  # real run, cheapest path for thousands of messages
  python -m style_ft.generate_pairs --input data/processed/my_messages.jsonl \
      --output data/processed/pairs.jsonl --provider anthropic --batch
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import providers
from .jsonlio import log, read_jsonl
from .prompts import input_styles


def message_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def load_done_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {rec["id"] for rec in read_jsonl(path) if "id" in rec}


def _pair(rec: dict[str, Any], generated_input: str, input_style: str) -> dict[str, Any]:
    return {
        "id": message_id(rec["text"]),
        "input": generated_input.strip(),
        "output": rec["text"],
        "input_style": input_style,
        "meta": {
            "timestamp": rec.get("timestamp", ""),
            "thread_id": rec.get("thread_id", ""),
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="data/processed/my_messages.jsonl")
    ap.add_argument("--output", default="data/processed/pairs.jsonl")
    ap.add_argument("--provider", default="dummy", choices=["dummy", "anthropic"])
    ap.add_argument("--input-style", default="neutral", choices=input_styles())
    ap.add_argument("--model", default=providers.DEFAULT_MODEL)
    ap.add_argument("--max-tokens", type=int, default=providers.DEFAULT_MAX_TOKENS)
    ap.add_argument("--limit", type=int, default=None, help="only process the first N new messages")
    ap.add_argument("--concurrency", type=int, default=8, help="sync path only")
    ap.add_argument("--batch", action="store_true", help="use the Batches API (50%% cheaper, async)")
    ap.add_argument("--poll-seconds", type=int, default=60)
    ap.add_argument("--collect-batch-id", default=None, help="skip submission, collect an existing batch")
    ap.add_argument("--overwrite", action="store_true", help="ignore existing output and start fresh")
    args = ap.parse_args(argv)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.overwrite and out_path.exists():
        out_path.unlink()

    done = load_done_ids(out_path)
    records = [r for r in read_jsonl(args.input) if r.get("text")]
    todo = [r for r in records if message_id(r["text"]) not in done]
    if args.limit:
        todo = todo[: args.limit]
    log(f"{len(records)} messages, {len(done)} already generated, {len(todo)} to do")

    if not todo and not args.collect_batch_id:
        log("nothing to do")
        return 0

    # Append as results arrive so a crash never loses completed work.
    with out_path.open("a", encoding="utf-8") as fh:

        def emit(rec: dict[str, Any], generated: str | None) -> None:
            if not generated:
                return
            fh.write(json.dumps(_pair(rec, generated, args.input_style), ensure_ascii=False) + "\n")
            fh.flush()

        if args.provider == "dummy":
            for rec in todo:
                emit(rec, providers.dummy_neutralize(rec["text"], args.input_style))

        elif args.batch or args.collect_batch_id:
            anthropic = providers._require_anthropic()
            client = anthropic.Anthropic()
            by_id = {message_id(r["text"]): r for r in records}
            if args.collect_batch_id:
                results = providers.anthropic_collect_batch(
                    client, args.collect_batch_id, args.poll_seconds
                )
            else:
                results = providers.anthropic_generate_batch(
                    client,
                    [(message_id(r["text"]), r["text"]) for r in todo],
                    args.input_style,
                    args.model,
                    args.max_tokens,
                    args.poll_seconds,
                    on_batch_created=lambda bid: log(
                        f"resume later with: --collect-batch-id {bid}"
                    ),
                )
            for cid, generated in results.items():
                if cid in by_id and cid not in done:
                    emit(by_id[cid], generated)

        else:
            anthropic = providers._require_anthropic()
            client = anthropic.Anthropic()

            def work(rec: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
                return rec, providers.anthropic_generate_one(
                    client, rec["text"], args.input_style, args.model, args.max_tokens
                )

            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                for i, (rec, generated) in enumerate(pool.map(work, todo), 1):
                    emit(rec, generated)
                    if i % 50 == 0:
                        log(f"{i}/{len(todo)}")

    total = sum(1 for _ in read_jsonl(out_path))
    log(f"{total} pairs in {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
