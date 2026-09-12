"""Step 2b: keep only my outgoing messages, drop low-signal ones, dedup.

Input:  intermediate JSONL from parse_export.py
Output: JSONL of candidate style samples (same schema, filtered)

Every threshold is configurable; defaults are conservative starting points.

Usage:
  python -m style_ft.filter_messages \
      --input data/interim/messages.jsonl \
      --output data/processed/my_messages.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from .jsonlio import log, read_jsonl, write_jsonl

# Messages that carry no style signal. Compared after lowercasing and
# stripping punctuation, so "ok!!!" matches "ok".
DEFAULT_STOPLIST = {
    "ok", "okay", "k", "kk", "yeah", "yea", "yep", "yup", "ya", "no", "nope", "nah",
    "lol", "lmao", "haha", "hahaha", "hah", "lmfao",
    "omw", "otw", "brb", "ttyl", "idk", "ikr", "wyd", "hbu", "wbu",
    "thanks", "thank you", "thx", "ty", "np", "yw",
    "hi", "hey", "hello", "yo", "sup", "bye", "cya",
    "sure", "cool", "nice", "same", "true", "facts", "word", "bet", "fr",
    "yes", "y", "n", "done", "got it", "gotcha", "sounds good", "sg",
    "?", "??", "!", "!!",
}

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
# Placeholders exports leave behind for media/system events.
ATTACHMENT_RE = re.compile(
    r"^\s*(\[?(image|video|audio|sticker|attachment|photo|gif|contact card|"
    r"location|missed call|voice message)[^\]]*\]?|"
    r"￼|<media omitted>|this message was deleted|"
    r"liked “.*”|loved “.*”|emphasized “.*”|"
    r"laughed at “.*”|questioned “.*”|disliked “.*”)\s*$",
    re.IGNORECASE | re.DOTALL,
)
PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace, for matching only."""
    text = unicodedata.normalize("NFKC", text).lower()
    return WS_RE.sub(" ", PUNCT_RE.sub(" ", text)).strip()


def is_symbol_only(text: str) -> bool:
    """True when nothing is left after removing emoji, punctuation and whitespace."""
    return not any(ch.isalnum() for ch in unicodedata.normalize("NFKC", text))


def load_stoplist(path: str | None, extra: str | None) -> set[str]:
    stoplist = set(DEFAULT_STOPLIST)
    if path:
        stoplist = {
            line.strip().lower()
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
    if extra:
        stoplist |= {w.strip().lower() for w in extra.split(",") if w.strip()}
    return {normalize(w) for w in stoplist}


def is_mine(rec: dict[str, Any], sender_names: list[str], assume_all_mine: bool) -> bool:
    if assume_all_mine:
        return True
    if sender_names:
        sender = rec.get("sender", "").strip().lower()
        return any(name in sender for name in sender_names)
    return rec.get("direction") == "out"


def filter_messages(
    rows: Iterable[dict[str, Any]],
    *,
    min_chars: int = 25,
    min_words: int = 5,
    max_chars: int = 1000,
    stoplist: set[str] | None = None,
    sender_names: list[str] | None = None,
    assume_all_mine: bool = False,
    drop_urls: bool = True,
    dedup: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stoplist = DEFAULT_STOPLIST if stoplist is None else stoplist
    sender_names = [n.lower() for n in (sender_names or [])]
    stats = {
        "total": 0, "not_mine": 0, "attachment": 0, "symbol_only": 0,
        "url_only": 0, "too_short": 0, "too_long": 0, "stoplisted": 0,
        "duplicate": 0, "kept": 0,
    }
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []

    for rec in rows:
        stats["total"] += 1
        text = rec.get("text", "").strip()

        if not is_mine(rec, sender_names, assume_all_mine):
            stats["not_mine"] += 1
            continue
        if ATTACHMENT_RE.match(text):
            stats["attachment"] += 1
            continue
        if is_symbol_only(text):
            stats["symbol_only"] += 1
            continue
        if drop_urls and not URL_RE.sub("", text).strip():
            stats["url_only"] += 1
            continue

        norm = normalize(text)
        if norm in stoplist:
            stats["stoplisted"] += 1
            continue
        if len(text) < min_chars or len(norm.split()) < min_words:
            stats["too_short"] += 1
            continue
        if len(text) > max_chars:
            stats["too_long"] += 1
            continue
        if dedup:
            if norm in seen:
                stats["duplicate"] += 1
                continue
            seen.add(norm)

        stats["kept"] += 1
        kept.append(rec)

    return kept, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="data/interim/messages.jsonl")
    ap.add_argument("--output", default="data/processed/my_messages.jsonl")
    ap.add_argument("--min-chars", type=int, default=25)
    ap.add_argument("--min-words", type=int, default=5)
    ap.add_argument("--max-chars", type=int, default=1000)
    ap.add_argument("--stoplist-file", default=None, help="newline-separated; replaces the default list")
    ap.add_argument("--extra-stopwords", default=None, help="comma-separated additions")
    ap.add_argument(
        "--sender-name",
        action="append",
        default=[],
        help="substring of your own sender name; use when the export has no direction column (repeatable)",
    )
    ap.add_argument("--assume-all-mine", action="store_true", help="input is already outgoing-only")
    ap.add_argument("--keep-urls", action="store_true", help="keep messages that are only a link")
    ap.add_argument("--no-dedup", action="store_true")
    ap.add_argument("--stats-out", default=None, help="write the stats dict to this JSON file")
    args = ap.parse_args(argv)

    kept, stats = filter_messages(
        read_jsonl(args.input),
        min_chars=args.min_chars,
        min_words=args.min_words,
        max_chars=args.max_chars,
        stoplist=load_stoplist(args.stoplist_file, args.extra_stopwords),
        sender_names=args.sender_name,
        assume_all_mine=args.assume_all_mine,
        drop_urls=not args.keep_urls,
        dedup=not args.no_dedup,
    )
    write_jsonl(args.output, kept)
    log(f"kept {stats['kept']}/{stats['total']} -> {args.output}")
    log(json.dumps(stats, indent=2))
    if args.stats_out:
        Path(args.stats_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.stats_out).write_text(json.dumps(stats, indent=2), encoding="utf-8")
    if stats["kept"] < 50:
        log("WARNING: fewer than 50 samples. Loosen --min-chars/--min-words or check the direction detection.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
