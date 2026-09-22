"""Surface-style metrics and the report renderer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from style_ft.common.jsonlio import write_jsonl
from style_ft.evaluation import metrics, runner


class TestMetrics(unittest.TestCase):
    def test_features(self) -> None:
        f = metrics.message_features("omw rn lol 😂")
        self.assertGreater(f["abbrev_ratio"], 0)
        self.assertGreater(f["emoji_per_100w"], 0)
        self.assertEqual(metrics.message_features("")["words"], 0)

    def test_compare_prefers_closer(self) -> None:
        ref = ["omw rn lol", "idk tbh lol"]
        base = ["I am on my way right now.", "I do not know honestly."]
        tuned = ["omw rn lol", "idk tbh"]
        result = metrics.compare(ref, base, tuned)
        self.assertEqual(result["abbrev_ratio"]["closer"], "tuned")


class TestReport(unittest.TestCase):
    def test_report_writes_files(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        preds = tmp / "predictions.jsonl"
        write_jsonl(preds, [{
            "id": "a", "input": "Friend 1: you around | later\nor no",
            "reference": "yo u free", "base_output": "Are you available?",
            "tuned_output": "yo u free rn",
        }])
        runner.main(["report", "--predictions", str(preds), "--outdir", str(tmp)])
        md = (tmp / "eval_report.md").read_text(encoding="utf-8")
        self.assertIn("Side by side", md)
        self.assertIn("\\|", md)  # pipe escaped, table not broken
        self.assertTrue((tmp / "eval_report.csv").exists())
        self.assertTrue(json.loads((tmp / "eval_metrics.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
