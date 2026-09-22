"""Corpus construction: cleaning, turn bursts, context pairing, the split.

The cleaning cases are artifacts found in the real corpus, not hypothetical ones.
"""

from __future__ import annotations

import json
import unittest
from typing import Any, ClassVar

from style_ft.corpus import chat_pairs, clean_messages, split_dataset
from style_ft.modeling import formatting


class TestCleanMessages(unittest.TestCase):
    def rows(self, *texts):
        return [
            {"text": t, "direction": d, "thread_id": "t1",
             "timestamp": f"2026-01-01T10:{i:02d}:00+00:00"}
            for i, (t, d) in enumerate(texts)
        ]

    def test_edit_replaces_the_message_it_corrects(self) -> None:
        # chat.db writes an edit as its own row after the original, so the
        # corpus holds both the typo and the fix.
        rows = self.rows(("i have to show u the thing said kelsey", "out"),
                         ('Edited to “i have to show u the thing since kelsey”', "out"))
        out, stats = clean_messages.clean(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "i have to show u the thing since kelsey")
        self.assertEqual(stats["edits_applied"], 1)

    def test_edit_applies_to_the_same_speaker(self) -> None:
        rows = self.rows(("You seen her ?", "in"), ("bruh", "out"),
                         ('Edited to “It’s crazy \U0001f480”', "in"))
        out, _ = clean_messages.clean(rows)
        texts = [r["text"] for r in out]
        self.assertIn("It’s crazy \U0001f480", texts)
        self.assertIn("bruh", texts)
        self.assertNotIn("You seen her ?", texts)

    def test_orphan_edit_is_kept_as_its_text(self) -> None:
        out, stats = clean_messages.clean(self.rows(('Edited to “ok”', "in")))
        self.assertEqual([r["text"] for r in out], ["ok"])
        self.assertEqual(stats["edits_orphaned"], 1)

    def test_drops_attachment_only_and_pasted_and_dupes(self) -> None:
        rows = self.rows(("￼", "out"), ("x" * 400, "out"),
                         ("same", "out"), ("same", "out"),
                         ("https://example.com", "out"),
                         ('Liked “nice”', "in"))
        out, stats = clean_messages.clean(rows)
        self.assertEqual([r["text"] for r in out], ["same"])
        for key in ("empty", "pasted", "duplicate", "url_only", "reaction"):
            self.assertEqual(stats[key], 1, key)

    def test_keeps_emoji_only_and_one_word(self) -> None:
        # 9% of this person's messages; style, not noise.
        out, _ = clean_messages.clean(self.rows(("\U0001f62d\U0001f62d", "out"), ("nah", "out")))
        self.assertEqual([r["text"] for r in out], ["\U0001f62d\U0001f62d", "nah"])

    def test_same_text_not_consecutive_is_kept(self) -> None:
        out, _ = clean_messages.clean(
            self.rows(("ok", "out"), ("then what", "in"), ("ok", "out")))
        self.assertEqual(len(out), 3)


class TestChatPairs(unittest.TestCase):
    THREAD: ClassVar[list[dict[str, Any]]] = [
        {"text": "yo", "direction": "in", "sender": "+14155550001", "thread_id": "+14155550001",
         "timestamp": "2026-01-01T10:00:00+00:00", "is_group": False},
        {"text": "u up", "direction": "in", "sender": "+14155550001", "thread_id": "+14155550001",
         "timestamp": "2026-01-01T10:00:30+00:00", "is_group": False},
        {"text": "ya", "direction": "out", "sender": "Me", "thread_id": "+14155550001",
         "timestamp": "2026-01-01T10:01:00+00:00", "is_group": False},
        {"text": "barely", "direction": "out", "sender": "Me", "thread_id": "+14155550001",
         "timestamp": "2026-01-01T10:01:20+00:00", "is_group": False},
        # Next day: a new conversation, not a reply to the above.
        {"text": "morning", "direction": "out", "sender": "Me", "thread_id": "+14155550001",
         "timestamp": "2026-01-02T09:00:00+00:00", "is_group": False},
    ]

    def test_bursts_become_one_turn(self) -> None:
        turns = chat_pairs.to_turns(self.THREAD)
        self.assertEqual(turns[0]["text"], "yo\nu up")
        self.assertEqual(turns[1]["text"], "ya\nbarely")

    def test_pairs_carry_context_and_persona(self) -> None:
        pairs, _ = chat_pairs.build_pairs(self.THREAD, my_name="Cyrus")
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertEqual(pair["output"], "ya\nbarely")
        self.assertEqual([t["speaker"] for t in pair["context"]], ["Friend 1"])
        # The raw handle never reaches the training text.
        self.assertNotIn("+1415", json.dumps(pair["context"]))
        self.assertEqual(pair["meta"]["with"], "Friend 1")

    def test_session_gap_drops_orphan_openings(self) -> None:
        # "morning" the next day has no usable context, so it is not a pair.
        pairs, stats = chat_pairs.build_pairs(self.THREAD, my_name="Cyrus")
        self.assertEqual(stats["no_context"], 1)
        self.assertTrue(all(p["output"] != "morning" for p in pairs))

    def test_contacts_override_aliases(self) -> None:
        pairs, _ = chat_pairs.build_pairs(
            self.THREAD, my_name="Cyrus", contacts={"+14155550001": "Mom"})
        self.assertEqual(pairs[0]["meta"]["with"], "Mom")
        self.assertEqual(pairs[0]["context"][0]["speaker"], "Mom")

    def test_chat_messages_shape(self) -> None:
        pairs, _ = chat_pairs.build_pairs(self.THREAD, my_name="Cyrus")
        msgs = formatting.messages_for(pairs[0], my_name="Cyrus")
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant"])
        self.assertIn("You are Cyrus", msgs[0]["content"])
        self.assertIn("texting Friend 1", msgs[0]["content"])
        self.assertEqual(msgs[2]["content"], "ya\nbarely")


class TestSplit(unittest.TestCase):
    def test_deterministic_and_disjoint(self) -> None:
        rows = [{"id": f"id{i}", "context": [], "output": "b"} for i in range(1000)]
        train1, test1 = split_dataset.split(rows, 0.2)
        _, test2 = split_dataset.split(rows, 0.2)
        self.assertEqual([r["id"] for r in test1], [r["id"] for r in test2])
        self.assertEqual(len(train1) + len(test1), 1000)
        self.assertFalse({r["id"] for r in train1} & {r["id"] for r in test1})
        self.assertTrue(150 < len(test1) < 250, len(test1))

    def test_growing_dataset_keeps_assignments(self) -> None:
        # Adding data must not reshuffle what was already held out, or every
        # run's numbers are measured against a different test set.
        rows = [{"id": f"id{i}"} for i in range(200)]
        _, test_small = split_dataset.split(rows, 0.2)
        _, test_big = split_dataset.split(rows + [{"id": f"new{i}"} for i in range(200)], 0.2)
        self.assertTrue({r["id"] for r in test_small} <= {r["id"] for r in test_big})


if __name__ == "__main__":
    unittest.main(verbosity=2)
