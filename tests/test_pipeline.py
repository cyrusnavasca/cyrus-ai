"""End-to-end tests for the parts that run without a GPU or an API key."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from style_ft import dummy_data, evaluate, providers, split_dataset, style_metrics  # noqa: E402
from style_ft.filter_messages import filter_messages, is_symbol_only, normalize  # noqa: E402
from style_ft.formatting import to_messages  # noqa: E402
from style_ft.generate_pairs import message_id  # noqa: E402
from style_ft.jsonlio import read_jsonl, write_jsonl  # noqa: E402
from style_ft.parse_export import parse_export  # noqa: E402
from style_ft.prompts import build_user_prompt, input_styles  # noqa: E402


class TestParse(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        dummy_data.write_csv(self.tmp / "export.csv")
        dummy_data.write_xml(self.tmp / "export.xml")

    def test_csv_and_xml_agree(self) -> None:
        csv_rows = list(parse_export(self.tmp / "export.csv"))
        xml_rows = list(parse_export(self.tmp / "export.xml"))
        self.assertEqual(len(csv_rows), len(dummy_data.CONVERSATION))
        self.assertEqual([r["text"] for r in csv_rows], [r["text"] for r in xml_rows])
        self.assertEqual([r["direction"] for r in csv_rows], [r["direction"] for r in xml_rows])

    def test_direction_and_timestamp(self) -> None:
        rows = list(parse_export(self.tmp / "export.csv"))
        self.assertEqual(rows[0]["direction"], "in")
        self.assertEqual(rows[1]["direction"], "out")
        self.assertTrue(rows[0]["timestamp"].startswith("20"), rows[0]["timestamp"])

    def test_json_input(self) -> None:
        path = self.tmp / "export.json"
        path.write_text(json.dumps([
            {"body": "hello there friend", "date": 1700000000, "address": "+1555", "type": "2"}
        ]), encoding="utf-8")
        rows = list(parse_export(path))
        self.assertEqual(rows[0]["text"], "hello there friend")
        self.assertEqual(rows[0]["direction"], "out")

    def test_column_map_override(self) -> None:
        path = self.tmp / "weird.csv"
        path.write_text("Blah,Whatever\nsome long message body here,x\n", encoding="utf-8")
        rows = list(parse_export(path, column_map={"text": ["Blah"]}))
        self.assertEqual(rows[0]["text"], "some long message body here")


class TestFilter(unittest.TestCase):
    def test_drops_incoming_and_noise(self) -> None:
        rows = [
            {"text": "yeah this is a real message with plenty of substance to it", "direction": "out", "sender": "Me"},
            {"text": "this one is theirs and should be dropped entirely ok", "direction": "in", "sender": "Sam"},
            {"text": "lol", "direction": "out", "sender": "Me"},
            {"text": "😂😂", "direction": "out", "sender": "Me"},
            {"text": "https://example.com/x", "direction": "out", "sender": "Me"},
            {"text": "[Image]", "direction": "out", "sender": "Me"},
            {"text": "yeah this is a real message with plenty of substance to it", "direction": "out", "sender": "Me"},
        ]
        kept, stats = filter_messages(rows)
        self.assertEqual(stats["kept"], 1)
        self.assertEqual(stats["not_mine"], 1)
        self.assertEqual(stats["stoplisted"], 1)
        self.assertEqual(stats["symbol_only"], 1)
        self.assertEqual(stats["url_only"], 1)
        self.assertEqual(stats["attachment"], 1)
        self.assertEqual(stats["duplicate"], 1)
        self.assertEqual(len(kept), 1)

    def test_sender_name_fallback(self) -> None:
        rows = [
            {"text": "a reasonably long message written by me here", "direction": "unknown", "sender": "Cyrus N"},
            {"text": "a reasonably long message written by them here", "direction": "unknown", "sender": "Sam Rivera"},
        ]
        kept, _ = filter_messages(rows, sender_names=["cyrus"])
        self.assertEqual(len(kept), 1)
        self.assertIn("by me", kept[0]["text"])

    def test_thresholds_are_configurable(self) -> None:
        rows = [{"text": "short one", "direction": "out", "sender": "Me"}]
        self.assertEqual(filter_messages(rows)[1]["kept"], 0)
        self.assertEqual(filter_messages(rows, min_chars=1, min_words=1)[1]["kept"], 1)

    def test_normalize_and_symbol_only(self) -> None:
        self.assertEqual(normalize("Ok!!!"), "ok")
        self.assertTrue(is_symbol_only("🙃 ... !"))
        self.assertFalse(is_symbol_only("ok 🙃"))


class TestPairs(unittest.TestCase):
    def test_dummy_provider_strips_style(self) -> None:
        out = providers.dummy_neutralize("omw rn, u ready?? 😂")
        self.assertNotIn("😂", out)
        self.assertIn("you", out)
        self.assertNotIn("??", out)

    def test_dummy_provider_styles(self) -> None:
        msg = "first thing happened. second thing too."
        self.assertTrue(providers.dummy_neutralize(msg, "bullet").startswith("- "))
        self.assertTrue(providers.dummy_neutralize(msg, "topic").startswith("Respond about:"))

    def test_message_id_stable(self) -> None:
        self.assertEqual(message_id("hello"), message_id("hello"))
        self.assertNotEqual(message_id("hello"), message_id("hello "))

    def test_prompt_builds_for_every_style(self) -> None:
        for style in input_styles():
            prompt = build_user_prompt("hey what's up", style)
            self.assertIn("<message>", prompt)
            self.assertIn("hey what's up", prompt)
        with self.assertRaises(ValueError):
            build_user_prompt("x", "nope")

    def test_request_params_shape(self) -> None:
        params = providers.make_request_params("hi", "neutral", "claude-opus-5", 2048)
        self.assertEqual(params["model"], "claude-opus-5")
        self.assertEqual(params["messages"][0]["role"], "user")
        self.assertIn("effort", params["output_config"])


class TestSplit(unittest.TestCase):
    def test_deterministic_and_disjoint(self) -> None:
        rows = [{"id": f"id{i}", "input": "a", "output": "b"} for i in range(1000)]
        train1, test1 = split_dataset.split(rows, 0.2)
        train2, test2 = split_dataset.split(rows, 0.2)
        self.assertEqual([r["id"] for r in test1], [r["id"] for r in test2])
        self.assertEqual(len(train1) + len(test1), 1000)
        self.assertFalse({r["id"] for r in train1} & {r["id"] for r in test1})
        self.assertTrue(150 < len(test1) < 250, len(test1))

    def test_growing_dataset_keeps_assignments(self) -> None:
        rows = [{"id": f"id{i}"} for i in range(200)]
        _, test_small = split_dataset.split(rows, 0.2)
        _, test_big = split_dataset.split(rows + [{"id": f"new{i}"} for i in range(200)], 0.2)
        small_ids = {r["id"] for r in test_small}
        self.assertTrue(small_ids <= {r["id"] for r in test_big})


class TestFormatting(unittest.TestCase):
    def test_roundtrip(self) -> None:
        msgs = to_messages({"input": "Say hello.", "output": "yo wassup"})
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant"])
        self.assertIn("Say hello.", msgs[1]["content"])
        self.assertEqual(msgs[2]["content"], "yo wassup")
        self.assertEqual(len(to_messages({"input": "x", "output": "y"}, include_response=False)), 2)


class TestMetricsAndReport(unittest.TestCase):
    def test_features(self) -> None:
        f = style_metrics.message_features("omw rn lol 😂")
        self.assertGreater(f["abbrev_ratio"], 0)
        self.assertGreater(f["emoji_per_100w"], 0)
        self.assertEqual(style_metrics.message_features("")["words"], 0)

    def test_compare_prefers_closer(self) -> None:
        ref = ["omw rn lol", "idk tbh lol"]
        base = ["I am on my way right now.", "I do not know honestly."]
        tuned = ["omw rn lol", "idk tbh"]
        result = style_metrics.compare(ref, base, tuned)
        self.assertEqual(result["abbrev_ratio"]["closer"], "tuned")

    def test_report_writes_files(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        preds = tmp / "predictions.jsonl"
        write_jsonl(preds, [{
            "id": "a", "input": "Ask about | pipes\nand newlines",
            "reference": "yo u free", "base_output": "Are you available?",
            "tuned_output": "yo u free rn",
        }])
        evaluate.main(["report", "--predictions", str(preds), "--outdir", str(tmp)])
        md = (tmp / "eval_report.md").read_text(encoding="utf-8")
        self.assertIn("Side by side", md)
        self.assertIn("\\|", md)  # pipe escaped, table not broken
        self.assertTrue((tmp / "eval_report.csv").exists())
        self.assertTrue(json.loads((tmp / "eval_metrics.json").read_text(encoding="utf-8")))


class TestJsonl(unittest.TestCase):
    def test_roundtrip_and_bad_json(self) -> None:
        tmp = Path(tempfile.mkdtemp()) / "x.jsonl"
        write_jsonl(tmp, [{"a": 1}, {"a": 2}])
        self.assertEqual([r["a"] for r in read_jsonl(tmp)], [1, 2])
        tmp.write_text("{nope}\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            list(read_jsonl(tmp))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestGenerationHygiene(unittest.TestCase):
    """clean_generated is the only thing standing between a small local model
    and a training set full of preambles."""

    def test_strips_preamble_and_quotes(self) -> None:
        self.assertEqual(
            providers.clean_generated("Here is the neutral version: I am on my way.", "omw rn"),
            "I am on my way.",
        )
        self.assertEqual(
            providers.clean_generated('"I will be there soon."', "omw"),
            "I will be there soon.",
        )

    def test_rejects_unusable(self) -> None:
        # verbatim echo: no style was stripped
        self.assertIsNone(providers.clean_generated("omw rn", "omw rn"))
        # leaked slang the input is supposed to be free of
        self.assertIsNone(providers.clean_generated("lmk when you are here", "lmk when ur here"))
        # leaked emoji
        self.assertIsNone(providers.clean_generated("I am laughing 😂", "lol 😂"))
        # runaway generation
        self.assertIsNone(providers.clean_generated("x" * 500, "hi"))
        self.assertIsNone(providers.clean_generated("", "hi"))

    def test_bullet_style_must_be_bullets(self) -> None:
        self.assertIsNone(providers.clean_generated("Not a bullet list.", "hi", "bullet"))
        self.assertEqual(
            providers.clean_generated("- Running late\n- Start without me", "late, start w/o me", "bullet"),
            "- Running late\n- Start without me",
        )

    def test_check_rejects_missing_model(self) -> None:
        with self.assertRaises(SystemExit):
            providers.ollama_check("http://127.0.0.1:1", "llama3.2")
