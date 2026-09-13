"""Tests for thread selection and per-thread capping."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from style_ft.select_threads import select, thread_summary  # noqa: E402


def msg(thread: str, i: int, mine: bool = True, group: bool = False) -> dict:
    return {
        "thread_id": thread,
        "text": f"{thread} message {i}",
        "timestamp": f"2025-01-{(i % 28) + 1:02d}T00:00:00",
        "direction": "out" if mine else "in",
        "is_group": group,
        "sender": "Me" if mine else thread,
    }


def corpus() -> list[dict]:
    rows = [msg("+1555dominant", i) for i in range(100)]
    rows += [msg("+1555dominant", i, mine=False) for i in range(50)]
    rows += [msg("+1555small", i) for i in range(10)]
    rows += [msg("work chat", i, group=True) for i in range(20)]
    rows += [msg("The Group", i, group=True) for i in range(30)]
    return rows


class TestSelect(unittest.TestCase):
    def test_summary_ranks_by_my_messages(self) -> None:
        summary = thread_summary(corpus())
        self.assertEqual(summary[0]["thread_id"], "+1555dominant")
        self.assertEqual(summary[0]["mine"], 100)
        self.assertEqual(summary[0]["total"], 150)
        self.assertEqual(summary[0]["kind"], "dm")
        self.assertEqual(next(s["kind"] for s in summary if s["thread_id"] == "The Group"), "group")

    def test_max_per_thread_caps_only_mine(self) -> None:
        kept, stats = select(corpus(), max_per_thread=25)
        by_thread: dict[str, int] = {}
        for row in kept:
            if row["direction"] == "out":
                by_thread[row["thread_id"]] = by_thread.get(row["thread_id"], 0) + 1
        self.assertEqual(by_thread["+1555dominant"], 25)
        self.assertEqual(by_thread["+1555small"], 10)  # under the cap, untouched
        self.assertEqual(by_thread["The Group"], 25)
        # 75 trimmed from the dominant thread + 5 from The Group
        self.assertEqual(stats["capped"], 80)
        # incoming messages from the capped thread are still present
        self.assertEqual(sum(1 for r in kept if r["thread_id"] == "+1555dominant"
                             and r["direction"] == "in"), 50)

    def test_cap_is_deterministic(self) -> None:
        first = [r["text"] for r in select(corpus(), max_per_thread=25)[0]]
        second = [r["text"] for r in select(corpus(), max_per_thread=25)[0]]
        self.assertEqual(first, second)
        other = [r["text"] for r in select(corpus(), max_per_thread=25, seed="other")[0]]
        self.assertNotEqual(first, other)

    def test_exclude_and_include(self) -> None:
        kept, _ = select(corpus(), exclude=["work"])
        self.assertNotIn("work chat", {r["thread_id"] for r in kept})
        kept, _ = select(corpus(), include=["dominant"])
        self.assertEqual({r["thread_id"] for r in kept}, {"+1555dominant"})

    def test_exclude_beats_include(self) -> None:
        kept, _ = select(corpus(), include=["+1555"], exclude=["small"])
        self.assertEqual({r["thread_id"] for r in kept}, {"+1555dominant"})

    def test_kind_filter(self) -> None:
        self.assertEqual(
            {r["thread_id"] for r in select(corpus(), kind="group")[0]},
            {"work chat", "The Group"},
        )
        self.assertNotIn("The Group", {r["thread_id"] for r in select(corpus(), kind="dm")[0]})

    def test_min_per_thread(self) -> None:
        kept, _ = select(corpus(), min_per_thread=15)
        self.assertNotIn("+1555small", {r["thread_id"] for r in kept})

    def test_outgoing_only(self) -> None:
        kept, _ = select(corpus(), outgoing_only=True)
        self.assertTrue(all(r["direction"] == "out" for r in kept))

    def test_output_sorted_by_time(self) -> None:
        kept, _ = select(corpus(), max_per_thread=10)
        self.assertEqual([r["timestamp"] for r in kept], sorted(r["timestamp"] for r in kept))


if __name__ == "__main__":
    unittest.main(verbosity=2)
