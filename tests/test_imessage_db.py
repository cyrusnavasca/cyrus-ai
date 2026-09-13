"""Tests for the chat.db reader, against a synthetic database with the same schema."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from style_ft.imessage_db import (  # noqa: E402
    apple_time_to_iso,
    decode_attributed_body,
    read_messages,
)

SCHEMA = """
CREATE TABLE message (
    ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, date INTEGER,
    is_from_me INTEGER, service TEXT, handle_id INTEGER,
    associated_message_type INTEGER DEFAULT 0, item_type INTEGER DEFAULT 0,
    balloon_bundle_id TEXT
);
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
CREATE TABLE chat (
    ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, display_name TEXT, style INTEGER
);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
"""


def make_body(text: str) -> bytes:
    """Minimal blob shaped like the NSString payload inside attributedBody."""
    raw = text.encode("utf-8")
    if len(raw) < 0x80:
        length = bytes([len(raw)])
    else:
        length = b"\x81" + len(raw).to_bytes(2, "little")
    return b"\x04\x0bstreamtyped" + b"NSString" + b"\x01\x94\x84\x01+" + length + raw


def build_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO handle VALUES (1, '+15551234567')")
    con.execute("INSERT INTO chat VALUES (1, 'chat-dm', NULL, 45)")
    con.execute("INSERT INTO chat VALUES (2, 'chat-group', 'The Group', 43)")
    # (rowid, text, body, apple_nanoseconds, is_from_me, chat, assoc_type, item_type, balloon)
    # Real databases use one encoding throughout; mixed encodings would break ORDER BY date.
    NS = 10**9
    rows = [
        (1, "hey from them", None, 700_000_000 * NS, 0, 1, 0, 0, None),
        (2, "mine with plain text", None, 700_000_060 * NS, 1, 1, 0, 0, None),
        (3, None, make_body("mine from attributedBody"), 700_000_120 * NS, 1, 1, 0, 0, None),
        (4, "Liked a message", None, 700_000_180 * NS, 1, 1, 2000, 0, None),  # tapback
        (5, "Sam joined", None, 700_000_240 * NS, 1, 1, 0, 1, None),          # system event
        (6, "app payload", None, 700_000_300 * NS, 1, 1, 0, 0, "com.apple.x"),  # balloon
        (7, "group message of mine", None, 700_000_360 * NS, 1, 2, 0, 0, None),
        (8, "", None, 700_000_420 * NS, 1, 1, 0, 0, None),                    # empty
    ]
    for rowid, text, body, date, mine, chat, assoc, item, balloon in rows:
        con.execute(
            "INSERT INTO message (ROWID, text, attributedBody, date, is_from_me, service,"
            " handle_id, associated_message_type, item_type, balloon_bundle_id)"
            " VALUES (?,?,?,?,?,'iMessage',1,?,?,?)",
            (rowid, text, body, date, mine, assoc, item, balloon),
        )
        con.execute("INSERT INTO chat_message_join VALUES (?, ?)", (chat, rowid))
    con.commit()
    con.close()


class TestChatDb(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Path(tempfile.mkdtemp()) / "chat.db"
        build_db(self.db)

    def test_filters_and_decoding(self) -> None:
        rows = list(read_messages(self.db))
        texts = [r["text"] for r in rows]
        self.assertEqual(
            texts,
            [
                "hey from them",
                "mine with plain text",
                "mine from attributedBody",
                "group message of mine",
            ],
        )
        self.assertEqual([r["direction"] for r in rows], ["in", "out", "out", "out"])
        self.assertEqual(rows[0]["sender"], "+15551234567")
        self.assertEqual(rows[3]["thread_id"], "The Group")
        self.assertTrue(rows[3]["is_group"])
        self.assertFalse(rows[0]["is_group"])

    def test_dms_only(self) -> None:
        rows = list(read_messages(self.db, dms_only=True))
        self.assertNotIn("group message of mine", [r["text"] for r in rows])

    def test_date_filters(self) -> None:
        all_rows = list(read_messages(self.db))
        day = all_rows[0]["timestamp"][:10]
        self.assertEqual(len(list(read_messages(self.db, since=day))), len(all_rows))
        self.assertEqual(list(read_messages(self.db, until="2001-01-01")), [])

    def test_apple_time(self) -> None:
        # Nanosecond and second encodings must produce the same instant.
        self.assertEqual(apple_time_to_iso(700_000_000), apple_time_to_iso(700_000_000 * 10**9))
        self.assertTrue(apple_time_to_iso(700_000_000).startswith("2023-"))
        self.assertEqual(apple_time_to_iso(None), "")

    def test_attributed_body_long_and_garbage(self) -> None:
        long_text = "x" * 500
        self.assertEqual(decode_attributed_body(make_body(long_text)), long_text)
        self.assertEqual(decode_attributed_body(make_body("emoji 😂 ok")), "emoji 😂 ok")
        self.assertEqual(decode_attributed_body(b"no string class here"), "")
        self.assertEqual(decode_attributed_body(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
