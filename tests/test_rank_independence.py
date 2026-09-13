"""Regression test: sampling stages must not share a hash namespace.

select_threads caps a thread by keeping its lowest-ranked messages.
sample_messages then takes the lowest-ranked messages overall. If both use the
same namespace, the capped thread's survivors are exactly what sampling
prefers, and the thread ends up massively over-represented - the opposite of
what capping is for.
"""

from __future__ import annotations

import collections
import statistics
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from style_ft.rank import stable_rank  # noqa: E402
from style_ft.sample_messages import sample  # noqa: E402
from style_ft.select_threads import select  # noqa: E402

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


class TestRankIndependence(unittest.TestCase):
    def test_namespaces_are_uncorrelated(self) -> None:
        texts = [f"message {i}" for i in range(2000)]
        a = sorted(texts, key=lambda t: stable_rank(t, "s", "select_threads"))[:500]
        b = sorted(texts, key=lambda t: stable_rank(t, "s", "sample_messages"))[:500]
        overlap = len(set(a) & set(b)) / 500
        self.assertLess(overlap, 0.40, f"namespaces correlated: {overlap:.2f} overlap")

    def test_same_namespace_is_deterministic(self) -> None:
        self.assertEqual(
            stable_rank("hello", "s", "select_threads"),
            stable_rank("hello", "s", "select_threads"),
        )
        self.assertNotEqual(
            stable_rank("hello", "s", "select_threads"),
            stable_rank("hello", "s", "sample_messages"),
        )

    def test_cap_survives_sampling(self) -> None:
        rows = corpus()
        capped, _ = select(rows, max_per_thread=500)
        share_after_cap = sum(1 for r in capped if r["thread_id"] == DOMINANT) / len(capped)
        picked, _ = sample(capped, 1000)
        share_after_sample = sum(1 for r in picked if r["thread_id"] == DOMINANT) / len(picked)
        # Sampling must preserve the capped proportion, not amplify it.
        self.assertAlmostEqual(share_after_sample, share_after_cap, delta=0.05)

    def test_capped_messages_are_not_rank_biased_for_the_next_stage(self) -> None:
        rows = corpus()
        capped, _ = select(rows, max_per_thread=500)
        dom = [stable_rank(r["text"], "style-ft", "sample_messages")
               for r in capped if r["thread_id"] == DOMINANT]
        rest = [stable_rank(r["text"], "style-ft", "sample_messages")
                for r in capped if r["thread_id"] != DOMINANT]
        self.assertAlmostEqual(statistics.fmean(dom), statistics.fmean(rest), delta=0.06)


if __name__ == "__main__":
    unittest.main(verbosity=2)
