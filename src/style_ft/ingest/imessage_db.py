"""Step 2a (macOS): read messages straight out of the Messages chat.db.

Better than a CSV/txt export: `is_from_me` is authoritative, timestamps are
exact, group vs 1:1 is known, and tapbacks/system events are distinguishable
instead of arriving as prose like "Liked “ok”".

Requires Full Disk Access for the app running this process (System Settings ->
Privacy & Security -> Full Disk Access). Without it the read fails with
"operation not permitted" or "no such table: message".

Usage:
  style-ft imessage --output data/interim/messages.jsonl
  style-ft imessage --since 2022-01-01 --dms-only
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..common.jsonlio import log, write_jsonl

DEFAULT_DB = Path.home() / "Library" / "Messages" / "chat.db"
# Apple's epoch is 2001-01-01 UTC.
APPLE_EPOCH_OFFSET = 978_307_200

# Participants per chat. Used to merge chat rows that Messages split apart when
# a group was renamed or its membership changed - those get a fresh ROWID and
# would otherwise look like unrelated conversations.
PARTICIPANTS_QUERY = """
SELECT j.chat_id AS chat_id, h.id AS handle
FROM chat_handle_join j
JOIN handle h ON h.ROWID = j.handle_id
"""

QUERY = """
SELECT
    m.ROWID                AS rowid,
    c.guid                 AS chat_guid,
    m.text                 AS text,
    m.attributedBody       AS attributed_body,
    m.date                 AS date,
    m.is_from_me           AS is_from_me,
    m.service              AS service,
    h.id                   AS handle,
    c.ROWID                AS chat_rowid,
    c.chat_identifier      AS chat_identifier,
    c.display_name         AS chat_display_name,
    c.style                AS chat_style
FROM message m
LEFT JOIN handle h            ON m.handle_id = h.ROWID
LEFT JOIN chat_message_join j ON j.message_id = m.ROWID
LEFT JOIN chat c              ON c.ROWID = j.chat_id
WHERE m.associated_message_type = 0   -- exclude tapbacks/reactions
  AND m.item_type = 0                 -- exclude joins, leaves, renames
  AND m.balloon_bundle_id IS NULL     -- exclude app payloads (games, Apple Pay...)
ORDER BY m.date ASC
"""


def apple_time_to_iso(raw: int | float | None) -> str:
    if not raw:
        return ""
    seconds = raw / 1e9 if raw > 1e11 else float(raw)  # ns since ~2012, else s
    try:
        return datetime.fromtimestamp(seconds + APPLE_EPOCH_OFFSET, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def decode_attributed_body(blob: bytes | None) -> str:
    """Pull the plain text out of an NSKeyedArchiver typedstream blob.

    Messages sent from recent OS versions leave `message.text` NULL and put the
    body here. We only need the string payload, so we parse the one length-
    prefixed NSString rather than implementing typedstream properly.
    """
    if not blob:
        return ""
    if b"NSString" not in blob:
        return ""
    try:
        tail = blob.split(b"NSString", 1)[1][5:]  # skip the class-marker bytes
        if not tail:
            return ""
        marker = tail[0]
        if marker == 0x81:  # 2-byte little-endian length
            length = int.from_bytes(tail[1:3], "little")
            start = 3
        elif marker == 0x92:  # 4-byte little-endian length
            length = int.from_bytes(tail[1:5], "little")
            start = 5
        else:
            length = marker
            start = 1
        text = tail[start : start + length].decode("utf-8", errors="ignore")
    except (IndexError, ValueError):
        return ""
    return text.strip()


def load_participants(con: sqlite3.Connection) -> dict[int, frozenset[str]]:
    out: dict[int, set[str]] = {}
    for row in con.execute(PARTICIPANTS_QUERY):
        out.setdefault(row["chat_id"], set()).add(row["handle"])
    return {k: frozenset(v) for k, v in out.items()}


def merge_similar(
    groups: dict[frozenset[str], list[int]], threshold: float = 0.7
) -> dict[int, int]:
    """Map each chat rowid to a canonical rowid, joining near-identical rosters.

    A renamed group keeps its roster exactly; a group that gained or lost one
    member does not, so exact-set matching alone still splits it. Jaccard
    overlap above the threshold treats those as the same conversation.
    """
    rosters = sorted(groups, key=lambda r: (-len(groups[r]), -len(r)))
    canonical: dict[frozenset[str], frozenset[str]] = {}
    for roster in rosters:
        for existing in canonical.values():
            if not roster or not existing:
                continue
            overlap = len(roster & existing) / len(roster | existing)
            if overlap >= threshold:
                canonical[roster] = existing
                break
        else:
            canonical[roster] = roster
    mapping: dict[int, int] = {}
    for roster, chat_ids in groups.items():
        target = canonical.get(roster, roster)
        head = min(groups.get(target, chat_ids))
        for chat_id in chat_ids:
            mapping[chat_id] = head
    return mapping


def open_db(db_path: Path, copy: bool = True) -> tuple[sqlite3.Connection, Path | None]:
    """Copy the db (plus WAL sidecars) to a temp dir before reading.

    Reading the live database while Messages is running can miss or lock rows;
    copying is cheap insurance and leaves the original untouched.
    """
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")
    if not copy:
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True), None
    tmpdir = Path(tempfile.mkdtemp(prefix="chatdb-"))
    try:
        shutil.copy2(db_path, tmpdir / "chat.db")
        for suffix in ("-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, tmpdir / ("chat.db" + suffix))
    except PermissionError as exc:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise SystemExit(
            f"Permission denied reading {db_path}.\n"
            "Grant Full Disk Access to the app running this process:\n"
            "  System Settings -> Privacy & Security -> Full Disk Access\n"
            "then fully quit and reopen that app."
        ) from exc
    return sqlite3.connect(tmpdir / "chat.db"), tmpdir


def read_messages(
    db_path: Path = DEFAULT_DB,
    since: str | None = None,
    until: str | None = None,
    dms_only: bool = False,
    copy: bool = True,
    thread_key: str = "participants",
    merge_threshold: float = 0.7,
) -> Iterator[dict[str, Any]]:
    """thread_key:
    "display"      - the chat's display name, or its identifier. Splits on rename.
    "participants" - the roster, with near-identical rosters merged. Survives
                     renames and small membership changes. Default.
    """
    con, tmpdir = open_db(db_path, copy)
    con.row_factory = sqlite3.Row
    try:
        participants: dict[int, frozenset[str]] = {}
        canonical: dict[int, int] = {}
        names: dict[int, str] = {}
        if thread_key == "participants":
            participants = load_participants(con)
            by_roster: dict[frozenset[str], list[int]] = {}
            for chat_id, roster in participants.items():
                by_roster.setdefault(roster, []).append(chat_id)
            canonical = merge_similar(by_roster, merge_threshold)
            # Name each merged thread after its most recently used display name.
            for row in con.execute(
                "SELECT c.ROWID AS rowid, c.display_name AS display_name,"
                " c.chat_identifier AS chat_identifier, MAX(m.date) AS last"
                " FROM chat c LEFT JOIN chat_message_join j ON j.chat_id = c.ROWID"
                " LEFT JOIN message m ON m.ROWID = j.message_id GROUP BY c.ROWID"
                " ORDER BY last ASC"
            ):
                head = canonical.get(row["rowid"], row["rowid"])
                label = row["display_name"] or row["chat_identifier"] or ""
                if label:
                    names[head] = label  # later rows overwrite, so newest wins
        try:
            rows = con.execute(QUERY)
        except sqlite3.OperationalError as exc:
            raise SystemExit(
                f"Could not query the messages database ({exc}).\n"
                "This is almost always missing Full Disk Access - see --help."
            ) from exc
        for row in rows:
            # chat.style: 43 = group, 45 = direct message.
            if dms_only and row["chat_style"] == 43:
                continue
            text = (row["text"] or "").strip() or decode_attributed_body(row["attributed_body"])
            if not text:
                continue
            timestamp = apple_time_to_iso(row["date"])
            # An undated row cannot be shown to fall inside the window, so a
            # date filter drops it rather than letting it through unchecked.
            if (since or until) and not timestamp:
                continue
            if since and timestamp[:10] < since:
                continue
            if until and timestamp[:10] > until:
                continue
            chat_rowid = row["chat_rowid"]
            if thread_key == "participants" and chat_rowid is not None:
                head = canonical.get(chat_rowid, chat_rowid)
                thread = names.get(head) or row["chat_identifier"] or ""
                roster = sorted(participants.get(chat_rowid, ()))
            else:
                thread = row["chat_display_name"] or row["chat_identifier"] or ""
                roster = sorted(participants.get(chat_rowid, ())) if participants else []
            is_mine = bool(row["is_from_me"])
            yield {
                "sender": "Me" if is_mine else (row["handle"] or ""),
                "text": text,
                "timestamp": timestamp,
                "thread_id": thread,
                "chat_guid": row["chat_guid"] or "",
                "participants": roster,
                "direction": "out" if is_mine else "in",
                "service": row["service"] or "",
                "is_group": row["chat_style"] == 43,
            }
    finally:
        con.close()
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--output", default="data/interim/messages.jsonl")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD, inclusive")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD, inclusive")
    ap.add_argument("--dms-only", action="store_true", help="skip group chats")
    ap.add_argument("--no-copy", action="store_true", help="read the live db in place (not recommended)")
    ap.add_argument(
        "--thread-key",
        default="participants",
        choices=["participants", "display"],
        help="participants: merge chat rows with near-identical rosters, so a renamed "
             "group stays one thread. display: key on the chat name (splits on rename)",
    )
    ap.add_argument("--merge-threshold", type=float, default=0.7,
                    help="roster overlap needed to treat two chat rows as one thread")
    args = ap.parse_args(argv)

    rows = list(
        read_messages(
            Path(args.db), args.since, args.until, args.dms_only, not args.no_copy,
            args.thread_key, args.merge_threshold,
        )
    )
    write_jsonl(args.output, rows)

    mine = sum(1 for r in rows if r["direction"] == "out")
    groups = sum(1 for r in rows if r["is_group"])
    log(f"{len(rows)} messages -> {args.output}")
    log(f"  mine: {mine}   theirs: {len(rows) - mine}   in group chats: {groups}")
    if rows:
        log(f"  range: {rows[0]['timestamp'][:10]} .. {rows[-1]['timestamp'][:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
