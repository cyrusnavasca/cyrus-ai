"""Step 2a: parse a raw message export into the standard intermediate format.

Supported inputs (auto-detected by extension, override with --format):
  csv  - iMazing / most CSV exports. Column names are configurable.
  xml  - SMS Backup & Restore (<sms> and <mms> elements).
  json - a JSON array, or JSONL, already close to the target shape.

Output: JSONL, one record per message:
  {"sender": str, "text": str, "timestamp": str, "thread_id": str, "direction": "out"|"in"|"unknown"}

Usage:
  python -m style_ft.parse_export --input data/raw/export.csv --output data/interim/messages.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from xml.etree import ElementTree as ET

from .jsonlio import log, write_jsonl

# Candidate source column names, in priority order. Matched case-insensitively
# after stripping non-alphanumerics, so "Message Date" matches "messagedate".
DEFAULT_COLUMN_MAP: dict[str, list[str]] = {
    "text": ["text", "body", "message", "messagetext", "content"],
    "timestamp": ["messagedate", "date", "timestamp", "time", "datesent", "readabledate"],
    "sender": ["sendername", "senderid", "sender", "from", "address", "handleid", "contact"],
    "thread_id": ["chatsession", "threadid", "chatid", "conversationid", "chat", "address"],
    "direction": ["type", "direction", "isfromme", "ismine", "sent"],
}

# Values that mean "this message was sent by the export's owner".
OUTGOING_TOKENS = {"outgoing", "sent", "out", "2", "true", "yes", "1:me", "me"}
INCOMING_TOKENS = {"incoming", "received", "in", "1", "false", "no", "inbox"}

CSV_SNIFF_BYTES = 8192


def _norm_key(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _resolve_columns(
    fieldnames: list[str], column_map: dict[str, list[str]]
) -> dict[str, str | None]:
    """Map each canonical field to an actual column name in the file."""
    lookup = {_norm_key(f): f for f in fieldnames if f}
    resolved: dict[str, str | None] = {}
    for canonical, candidates in column_map.items():
        resolved[canonical] = next(
            (lookup[c] for c in (_norm_key(x) for x in candidates) if c in lookup), None
        )
    return resolved


def _classify_direction(raw: str | None) -> str:
    """SMS Backup & Restore uses type=2 for sent; iMazing uses 'Outgoing'."""
    if raw is None:
        return "unknown"
    token = str(raw).strip().lower()
    if not token:
        return "unknown"
    if token in OUTGOING_TOKENS:
        return "out"
    if token in INCOMING_TOKENS:
        return "in"
    return "unknown"


def _normalize_timestamp(raw: Any) -> str:
    """Best-effort ISO 8601. Epoch millis/seconds and common date strings."""
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    if s.isdigit():
        val = int(s)
        # SMS Backup & Restore writes epoch milliseconds.
        if val > 10_000_000_000:
            val //= 1000
        try:
            return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return s
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%b %d, %Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
    ):
        try:
            return datetime.strptime(s.split("+")[0].strip(), fmt).isoformat()
        except ValueError:
            continue
    return s  # keep the original rather than lose the information


def _record(
    text: Any, timestamp: Any, sender: Any, thread_id: Any, direction: Any
) -> dict[str, str] | None:
    body = ("" if text is None else str(text)).strip()
    if not body:
        return None
    return {
        "sender": ("" if sender is None else str(sender)).strip(),
        "text": body,
        "timestamp": _normalize_timestamp(timestamp),
        "thread_id": ("" if thread_id is None else str(thread_id)).strip(),
        "direction": _classify_direction(direction),
    }


def parse_csv(path: Path, column_map: dict[str, list[str]]) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(CSV_SNIFF_BYTES)
        fh.seek(0)
        try:
            dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = "excel"
        reader = csv.DictReader(fh, dialect=dialect)
        if not reader.fieldnames:
            return
        cols = _resolve_columns(list(reader.fieldnames), column_map)
        if cols["text"] is None:
            raise SystemExit(
                f"No text column found in {path}. Columns: {reader.fieldnames}\n"
                "Pass --column-map '{\"text\": [\"YourColumn\"]}' to override."
            )
        log(f"csv column mapping: {cols}")
        for row in reader:
            rec = _record(
                row.get(cols["text"]),
                row.get(cols["timestamp"]) if cols["timestamp"] else None,
                row.get(cols["sender"]) if cols["sender"] else None,
                row.get(cols["thread_id"]) if cols["thread_id"] else None,
                row.get(cols["direction"]) if cols["direction"] else None,
            )
            if rec:
                yield rec


def parse_xml(path: Path) -> Iterator[dict[str, str]]:
    """SMS Backup & Restore. Streamed, since these files get large."""
    for _event, elem in ET.iterparse(str(path), events=("end",)):
        if elem.tag == "sms":
            rec = _record(
                elem.get("body"),
                elem.get("date"),
                elem.get("contact_name") or elem.get("address"),
                elem.get("address"),
                elem.get("type"),
            )
            if rec:
                yield rec
            elem.clear()
        elif elem.tag == "mms":
            text = next(
                (
                    p.get("text")
                    for p in elem.iter("part")
                    if p.get("ct") == "text/plain" and p.get("text")
                ),
                None,
            )
            # msg_box mirrors sms@type: 1 inbox, 2 sent.
            rec = _record(
                text,
                elem.get("date"),
                elem.get("contact_name") or elem.get("address"),
                elem.get("address"),
                elem.get("msg_box"),
            )
            if rec:
                yield rec
            elem.clear()


def parse_json(path: Path, column_map: dict[str, list[str]]) -> Iterator[dict[str, str]]:
    raw = path.read_text(encoding="utf-8").strip()
    rows: list[dict[str, Any]]
    if raw.startswith("["):
        rows = json.loads(raw)
    else:
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    for row in rows:
        cols = _resolve_columns(list(row.keys()), column_map)
        rec = _record(
            row.get(cols["text"]) if cols["text"] else None,
            row.get(cols["timestamp"]) if cols["timestamp"] else None,
            row.get(cols["sender"]) if cols["sender"] else None,
            row.get(cols["thread_id"]) if cols["thread_id"] else None,
            row.get(cols["direction"]) if cols["direction"] else None,
        )
        if rec:
            yield rec


def parse_export(
    path: str | Path, fmt: str = "auto", column_map: dict[str, list[str]] | None = None
) -> Iterator[dict[str, str]]:
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"Input not found: {path}")
    merged = {k: list(v) for k, v in DEFAULT_COLUMN_MAP.items()}
    for k, v in (column_map or {}).items():
        merged[k] = list(v) + merged.get(k, [])
    if fmt == "auto":
        fmt = path.suffix.lstrip(".").lower()
        if fmt in ("jsonl", "ndjson"):
            fmt = "json"
    if fmt == "csv":
        yield from parse_csv(path, merged)
    elif fmt == "xml":
        yield from parse_xml(path)
    elif fmt == "json":
        yield from parse_json(path, merged)
    else:
        raise SystemExit(f"Unsupported format {fmt!r}. Use --format csv|xml|json.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="raw export file")
    ap.add_argument("--output", default="data/interim/messages.jsonl")
    ap.add_argument("--format", default="auto", choices=["auto", "csv", "xml", "json"])
    ap.add_argument(
        "--column-map",
        default=None,
        help='JSON overriding source column names, e.g. \'{"text": ["Body"]}\'',
    )
    args = ap.parse_args(argv)

    column_map = json.loads(args.column_map) if args.column_map else None
    rows = list(parse_export(args.input, args.format, column_map))
    n = write_jsonl(args.output, rows)

    directions: dict[str, int] = {}
    for r in rows:
        directions[r["direction"]] = directions.get(r["direction"], 0) + 1
    log(f"parsed {n} messages -> {args.output}")
    log(f"direction counts: {directions}")
    if directions.get("unknown", 0) == n and n:
        log("WARNING: no direction detected. Filter step will need --sender-name or --direction-column.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
