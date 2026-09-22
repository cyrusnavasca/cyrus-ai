"""Step 1b: remove artifacts that would teach the model the wrong thing.

Run between the reader and the pair builders. Everything here was found by
looking at the real corpus rather than guessed at - most of the obvious
candidates (tapbacks, app payloads, attachment rows) are already excluded by
`imessage_db`'s query, so this handles what survives it.

What it does, and why:

  edits        chat.db stores a message edit as a *separate* row reading
               `Edited to "..."` right after the original, so the corpus holds
               both the typo and the correction. The edit is applied to the
               original and the artifact row dropped - this recovers the real
               message instead of discarding it.
  pasted       Song lyrics, an R Markdown lab report, the Bee Movie script.
               Rare, but they are the worst kind of target: long, not
               conversational, and they teach the model to write essays.
  duplicates   The same text sent twice in a row. Burst-joining would otherwise
               produce "x\\nx" as a single turn.
  urls         A bare link carries no voice in either direction.
  reactions    "Liked ...", "Emphasized ..." leaking in from SMS threads.

Deliberately kept: emoji-only and one-word messages. They are 9% of what this
person sends and are a real part of the style, not noise.

Usage:
  style-ft clean --input data/interim/selected.jsonl \
      --output data/interim/clean.jsonl
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..common.jsonlio import log, read_jsonl, write_jsonl

# The edit artifact, with the curly quotes chat.db actually writes.
EDIT_RE = re.compile(r'^Edited to\s*[“"](.*)[”"]\s*$', re.DOTALL)
REACTION_RE = re.compile(
    r"^(Liked|Loved|Laughed at|Emphasized|Disliked|Questioned|"
    r"Removed a heart from|Removed an exclamation from)\s+[“\"]",
    re.IGNORECASE,
)
URL_ONLY_RE = re.compile(r"^\s*https?://\S+\s*$", re.IGNORECASE)
ATTACHMENT_PLACEHOLDER = "￼"

# Above this a message is pasted content, not texting. The 99.9th percentile of
# this corpus is ~150 characters; 300 leaves plenty of headroom for a genuinely
# long text while still catching lyrics and homework.
DEFAULT_MAX_CHARS = 300

# An edit lands seconds after the message it corrects.
EDIT_WINDOW_SECONDS = 600


def _ts(row: dict[str, Any]) -> str:
    return row.get("timestamp", "") or ""


def clean(
    rows: Iterable[dict[str, Any]], max_chars: int = DEFAULT_MAX_CHARS
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_thread: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_thread[row.get("thread_id", "")].append(row)

    stats = collections.Counter()
    out: list[dict[str, Any]] = []

    for messages in by_thread.values():
        messages.sort(key=_ts)
        kept: list[dict[str, Any]] = []
        for row in messages:
            stats["input"] += 1
            text = (row.get("text") or "").replace(ATTACHMENT_PLACEHOLDER, " ").strip()
            if not text:
                stats["empty"] += 1
                continue

            edit = EDIT_RE.match(text)
            if edit:
                # Apply the correction to the most recent message from the same
                # speaker, which is the one it edits.
                replacement = edit.group(1).strip()
                for earlier in reversed(kept):
                    if earlier.get("direction") == row.get("direction"):
                        earlier["text"] = replacement
                        stats["edits_applied"] += 1
                        break
                else:
                    # No original in view (thread starts mid-edit): keep the
                    # corrected text rather than lose the message.
                    row = {**row, "text": replacement}
                    kept.append(row)
                    stats["edits_orphaned"] += 1
                continue

            if REACTION_RE.match(text):
                stats["reaction"] += 1
                continue
            if URL_ONLY_RE.match(text):
                stats["url_only"] += 1
                continue
            if len(text) > max_chars:
                stats["pasted"] += 1
                continue
            if kept and kept[-1].get("direction") == row.get("direction") and (
                kept[-1]["text"] == text
            ):
                stats["duplicate"] += 1
                continue

            kept.append({**row, "text": text})
        out.extend(kept)

    out.sort(key=_ts)
    stats["kept"] = len(out)
    return out, dict(stats)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--input", default="data/interim/selected.jsonl")
    ap.add_argument("--output", default="data/interim/clean.jsonl")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS,
                    help="drop messages longer than this as pasted content")
    ap.add_argument("--stats", default="outputs/clean_stats.json")
    args = ap.parse_args(argv)

    rows, stats = clean(read_jsonl(args.input), args.max_chars)
    write_jsonl(args.output, rows)
    Path(args.stats).parent.mkdir(parents=True, exist_ok=True)
    Path(args.stats).write_text(json.dumps(stats, indent=2), encoding="utf-8")
    log(json.dumps(stats, indent=2))
    log(f"{len(rows)} messages -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
