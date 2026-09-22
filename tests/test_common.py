"""JSONL contracts and the deterministic ranking the sampling stages share.

The ranking has one rule worth a regression test: two stages that both subsample
must not share a hash namespace. `select_threads` caps a thread by keeping its
lowest-ranked messages; if a later stage ranks with the same namespace, the
capped thread's survivors are exactly what it prefers, and the thread ends up
over-represented - the opposite of what capping is for.
"""

from __future__ import annotations

import statistics
import tempfile
import unittest
from pathlib import Path

from style_ft.common.jsonlio import read_jsonl, write_jsonl
from style_ft.common.rank import stable_rank
from style_ft.corpus.select_threads import select

DOMINANT = "+1555dominant"


def corpus() -> list[dict]:
    rows = []
    for i in range(4000):  # one huge thread
        rows.append({"thread_id": DOMINANT, "text": f"dominant {i}",
                     "timestamp": f"202{3 + i % 3}-06-01T00:00:00", "direction": "out"})
    for t in range(20):   # twenty ordinary ones
        for i in range(200):
            rows.append({"thread_id": f"thread{t}", "text": f"t{t} message {i}",
                         "timestamp": f"202{3 + i % 3}-06-01T00:00:00", "direction": "out"})
    return rows


class TestJsonl(unittest.TestCase):
    def test_roundtrip_and_bad_json(self) -> None:
        tmp = Path(tempfile.mkdtemp()) / "x.jsonl"
        write_jsonl(tmp, [{"a": 1}, {"a": 2}])
        self.assertEqual([r["a"] for r in read_jsonl(tmp)], [1, 2])
        tmp.write_text("{nope}\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            list(read_jsonl(tmp))

    def test_unicode_survives_a_roundtrip(self) -> None:
        # Emoji are 35% of this corpus's replies; an ensure_ascii slip would
        # escape them into the training data.
        tmp = Path(tempfile.mkdtemp()) / "x.jsonl"
        write_jsonl(tmp, [{"text": "😭💀"}])
        self.assertIn("😭", tmp.read_text(encoding="utf-8"))
        self.assertEqual(next(iter(read_jsonl(tmp)))["text"], "😭💀")


class TestRankIndependence(unittest.TestCase):
    def test_namespaces_are_uncorrelated(self) -> None:
        texts = [f"message {i}" for i in range(2000)]
        a = sorted(texts, key=lambda t: stable_rank(t, "s", "select_threads"))[:500]
        b = sorted(texts, key=lambda t: stable_rank(t, "s", "chatcap"))[:500]
        overlap = len(set(a) & set(b)) / 500
        self.assertLess(overlap, 0.40, f"namespaces correlated: {overlap:.2f} overlap")

    def test_same_namespace_is_deterministic(self) -> None:
        self.assertEqual(
            stable_rank("hello", "s", "select_threads"),
            stable_rank("hello", "s", "select_threads"),
        )
        self.assertNotEqual(
            stable_rank("hello", "s", "select_threads"),
            stable_rank("hello", "s", "chatcap"),
        )

    def test_capped_messages_are_not_rank_biased_for_the_next_stage(self) -> None:
        capped, _ = select(corpus(), max_per_thread=500)
        dom = [stable_rank(r["text"], "style-ft", "chatcap")
               for r in capped if r["thread_id"] == DOMINANT]
        rest = [stable_rank(r["text"], "style-ft", "chatcap")
                for r in capped if r["thread_id"] != DOMINANT]
        self.assertAlmostEqual(statistics.fmean(dom), statistics.fmean(rest), delta=0.06)


if __name__ == "__main__":
    unittest.main(verbosity=2)
