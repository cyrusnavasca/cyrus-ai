"""Step 3b: build conversational (context -> my reply) pairs straight from threads.

The style-transfer path (`generate_pairs`) teaches "reword this as me". This one
teaches "here is the conversation, say what I would say next" - the model has to
produce the content, not just restyle content it was handed.

No LLM is involved and nothing is sent anywhere: the input is the real messages
that preceded each of your replies, so there is no synthetic-input noise and no
per-message API cost.

Turns, not messages. People text in bursts ("wait", "actually", "nvm") and a
model trained to emit one fragment learns to stop mid-thought, so consecutive
messages from the same speaker within --burst-seconds are joined into one turn.

Usage:
  python -m style_ft.build_chat_pairs \
      --input data/interim/messages.jsonl \
      --output data/processed/chat_pairs.jsonl \
      --my-name Cyrus --contacts data/contacts.json --max-per-thread 2000

`--contacts` is an optional {"+14155550123": "Mom"} map. Without it the model is
told it is texting "+14155550123", which is exactly as useful as it sounds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .jsonlio import log, read_jsonl, write_jsonl
from .rank import stable_rank

# A reply to something said three days ago is a new conversation, not a reply.
DEFAULT_SESSION_GAP_MINUTES = 180
DEFAULT_BURST_SECONDS = 180
DEFAULT_CONTEXT_TURNS = 6
DEFAULT_MAX_CONTEXT_CHARS = 2000

# U+FFFC, what iMessage leaves behind where an attachment was.
_ATTACHMENT_PLACEHOLDER = "\ufffc"


def alias_for(handle: str, index: int) -> str:
    """A stable pseudonym for a handle with no contact name.

    Training on raw phone numbers is bad on two counts: the digits are noise the
    model memorizes instead of a person, and they end up in the weights of a
    model that may be served publicly.
    """
    return f"Friend {index}"


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def to_turns(
    messages: list[dict[str, Any]],
    burst_seconds: int = DEFAULT_BURST_SECONDS,
) -> list[dict[str, Any]]:
    """Collapse consecutive same-speaker messages into single turns."""
    turns: list[dict[str, Any]] = []
    for msg in messages:
        text = (msg.get("text") or "").replace(_ATTACHMENT_PLACEHOLDER, " ").strip()
        if not text:
            continue
        direction = msg.get("direction")
        sender = msg.get("sender") or ""
        ts = _parse_ts(msg.get("timestamp", ""))
        prev = turns[-1] if turns else None
        same_speaker = (
            prev is not None
            and prev["direction"] == direction
            # In a group chat two different people are not one turn.
            and prev["sender"] == sender
        )
        close_in_time = (
            same_speaker
            and ts is not None
            and prev["ts"] is not None
            and (ts - prev["ts"]).total_seconds() <= burst_seconds
        )
        if close_in_time:
            prev["text"] += "\n" + text
            prev["ts"] = ts
        else:
            turns.append({"direction": direction, "sender": sender, "text": text, "ts": ts})
    return turns


def build_pairs(
    rows: Iterable[dict[str, Any]],
    my_name: str = "Me",
    contacts: dict[str, str] | None = None,
    context_turns: int = DEFAULT_CONTEXT_TURNS,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    session_gap_minutes: int = DEFAULT_SESSION_GAP_MINUTES,
    burst_seconds: int = DEFAULT_BURST_SECONDS,
    max_per_thread: int | None = None,
    min_reply_chars: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    contacts = contacts or {}
    by_thread: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_thread.setdefault(row.get("thread_id", ""), []).append(row)

    # Anything that still looks like a raw handle gets a stable pseudonym, so a
    # missing contacts map degrades to "Friend 3" rather than a phone number.
    def looks_like_handle(name: str) -> bool:
        return name.startswith("+") or "@" in name

    aliases: dict[str, str] = {}
    for n, key in enumerate(sorted(k for k in by_thread if looks_like_handle(k)), 1):
        aliases[key] = alias_for(key, n)

    pairs: list[dict[str, Any]] = []
    kept_pairs: list[dict[str, Any]] = []
    stats = {"threads": 0, "turns": 0, "no_context": 0, "too_short": 0, "capped": 0}

    for thread_id, messages in by_thread.items():
        messages.sort(key=lambda r: r.get("timestamp", ""))
        turns = to_turns(messages, burst_seconds)
        stats["threads"] += 1
        stats["turns"] += len(turns)

        is_group = bool(messages[0].get("is_group"))
        # A group's thread_id is already its display name; a DM's is a phone number.
        who = contacts.get(thread_id) or aliases.get(thread_id, thread_id)

        thread_pairs: list[dict[str, Any]] = []
        for i, turn in enumerate(turns):
            if turn["direction"] != "out":
                continue
            if len(turn["text"]) < min_reply_chars:
                stats["too_short"] += 1
                continue

            # Walk back for context, stopping at a conversational gap: replying
            # to a thread that went quiet for hours is a new opening, not a reply,
            # and training on it teaches non-sequiturs.
            context: list[dict[str, str]] = []
            used = 0
            prev_ts = turn["ts"]
            for earlier in reversed(turns[max(0, i - context_turns) : i]):
                if earlier["ts"] is not None and prev_ts is not None:
                    gap = (prev_ts - earlier["ts"]).total_seconds() / 60
                    if gap > session_gap_minutes:
                        break
                used += len(earlier["text"])
                if used > max_context_chars:
                    break
                if earlier["direction"] == "out":
                    speaker = my_name
                elif is_group and earlier["sender"]:
                    sender = earlier["sender"]
                    speaker = contacts.get(sender) or (
                        aliases.setdefault(sender, alias_for(sender, len(aliases) + 1))
                        if looks_like_handle(sender)
                        else sender
                    )
                else:
                    speaker = who
                context.insert(0, {"speaker": speaker, "text": earlier["text"]})
                prev_ts = earlier["ts"]

            if not context:
                stats["no_context"] += 1
                continue

            pairs.append(
                {
                    "id": hashlib.sha1(
                        f"{thread_id}:{turn['ts']}:{turn['text']}".encode("utf-8")
                    ).hexdigest()[:16],
                    "context": context,
                    "output": turn["text"],
                    "meta": {
                        "thread_id": thread_id,
                        "with": who,
                        "is_group": is_group,
                        "timestamp": turn["ts"].isoformat() if turn["ts"] else "",
                    },
                }
            )
            thread_pairs.append(pairs[-1])

        # One relationship can be 40% of a corpus; uncapped, the fine-tune learns
        # that relationship rather than the person. Capping is deterministic so a
        # rerun keeps the same subset. Selection is by identity, not by id: two
        # identical replies in a thread hash alike and must be counted separately.
        if max_per_thread and len(thread_pairs) > max_per_thread:
            ordered = sorted(thread_pairs, key=lambda p: stable_rank(p["id"], "", "chatcap"))
            keep = {id(p) for p in ordered[:max_per_thread]}
            stats["capped"] += len(thread_pairs) - len(keep)
            kept_pairs.extend(p for p in thread_pairs if id(p) in keep)
        else:
            kept_pairs.extend(thread_pairs)

    return kept_pairs, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="data/interim/messages.jsonl")
    ap.add_argument("--output", default="data/processed/chat_pairs.jsonl")
    ap.add_argument("--my-name", default="Me", help="how you are labelled in the transcript")
    ap.add_argument("--contacts", default=None, help='JSON map, e.g. {"+14155550123": "Mom"}')
    ap.add_argument("--context-turns", type=int, default=DEFAULT_CONTEXT_TURNS)
    ap.add_argument("--max-context-chars", type=int, default=DEFAULT_MAX_CONTEXT_CHARS)
    ap.add_argument("--session-gap-minutes", type=int, default=DEFAULT_SESSION_GAP_MINUTES)
    ap.add_argument("--burst-seconds", type=int, default=DEFAULT_BURST_SECONDS)
    ap.add_argument("--max-per-thread", type=int, default=None)
    ap.add_argument("--stats", default="outputs/chat_pair_stats.json")
    args = ap.parse_args(argv)

    contacts = json.loads(Path(args.contacts).read_text()) if args.contacts else {}
    pairs, stats = build_pairs(
        read_jsonl(args.input),
        my_name=args.my_name,
        contacts=contacts,
        context_turns=args.context_turns,
        max_context_chars=args.max_context_chars,
        session_gap_minutes=args.session_gap_minutes,
        burst_seconds=args.burst_seconds,
        max_per_thread=args.max_per_thread,
    )
    write_jsonl(args.output, pairs)
    stats["pairs"] = len(pairs)
    Path(args.stats).parent.mkdir(parents=True, exist_ok=True)
    Path(args.stats).write_text(json.dumps(stats, indent=2), encoding="utf-8")
    log(json.dumps(stats, indent=2))
    log(f"{len(pairs)} chat pairs -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
