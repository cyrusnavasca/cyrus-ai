"""End-to-end tests for the parts that run without a GPU or an API key."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from style_ft import build_chat_pairs, clean_messages, dummy_data, evaluate, formatting, providers, split_dataset, style_metrics  # noqa: E402
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


class FakeTokenizer:
    """Minimal stand-in for a HF tokenizer: a Llama-3-shaped chat template and
    whitespace tokenization. Enough to pin the mask boundary without a GPU stack."""

    def __init__(self, prompt_is_prefix: bool = True) -> None:
        self.prompt_is_prefix = prompt_is_prefix

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False):
        parts = [f"<|{m['role']}|> {m['content']} <|end|>" for m in messages]
        if add_generation_prompt:
            parts.append("<|assistant|>")
        text = " ".join(parts)
        # Simulate a template that reorders roles, so the prompt is not a prefix.
        return text if self.prompt_is_prefix else text[::-1]

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": [abs(hash(w)) % 1000 for w in text.split()]}


class TestLossMasking(unittest.TestCase):
    PAIR = {"input": "I am on my way.", "output": "omw rn lol"}

    def test_only_the_assistant_turn_is_supervised(self) -> None:
        ex = formatting.masked_example(self.PAIR, FakeTokenizer(), 2048)
        self.assertEqual(len(ex["input_ids"]), len(ex["labels"]))
        self.assertEqual(len(ex["attention_mask"]), len(ex["labels"]))

        supervised = [t for t in ex["labels"] if t != formatting.IGNORE_INDEX]
        self.assertTrue(supervised, "nothing is supervised - the mask ate everything")
        # The supervised span is the tail, and it is the assistant turn only.
        self.assertEqual(supervised, ex["input_ids"][-len(supervised):])
        self.assertLess(len(supervised), len(ex["labels"]),
                        "prompt tokens are not masked")

        tok = FakeTokenizer()
        n_prompt = len(tok(tok.apply_chat_template(
            formatting.to_messages(self.PAIR, include_response=False),
            add_generation_prompt=True))["input_ids"])
        self.assertEqual(ex["labels"][:n_prompt], [formatting.IGNORE_INDEX] * n_prompt)

    def test_overlong_examples_are_dropped_not_truncated(self) -> None:
        # Truncating cuts the reply, not the prompt: it can mask every label
        # (NaN loss) and strips the EOS, teaching the model never to stop.
        self.assertIsNone(formatting.masked_example(self.PAIR, FakeTokenizer(), 4))

    def test_arrays_stay_aligned(self) -> None:
        ex = formatting.masked_example(self.PAIR, FakeTokenizer(), 2048)
        self.assertEqual(len(ex["input_ids"]), len(ex["labels"]))
        self.assertEqual(len(ex["attention_mask"]), len(ex["labels"]))

    def test_raises_when_prompt_is_not_a_prefix(self) -> None:
        with self.assertRaises(formatting.PromptNotAPrefixError):
            formatting.masked_example(self.PAIR, FakeTokenizer(prompt_is_prefix=False), 2048)


class TestGenerationHygieneLeaks(unittest.TestCase):
    """Cases seen in a real 7B run: the input was technically different from the
    message but still gave away the style the fine-tune is meant to learn."""

    def test_rejects_near_verbatim(self) -> None:
        self.assertIsNone(providers.clean_generated(
            "send me the contact photo", "and send me the contact photo"))
        self.assertIsNone(providers.clean_generated("omw!", "omw"))

    def test_deshouts_rather_than_dropping(self) -> None:
        # Rejecting these cost the corpus every all-caps example, and the first
        # fine-tune lost the user's ALL-CAPS register as a result. Keep the pair,
        # strip the giveaway from the input.
        self.assertEqual(
            providers.clean_generated(
                "MAYBE THAT WILL MAKE YOU DREAM MORE", "BECAUSE MAYBE THATLL MAKE U DREAM MORE"),
            "Maybe that will make you dream more")

    def test_initialisms_are_not_flattened(self) -> None:
        # Not shouting, so _unshout never runs and "AP" survives.
        self.assertEqual(
            providers.clean_generated(
                "Do you know the AP Literature homework?", "do u know what is ap lit hw"),
            "Do you know the AP Literature homework?")

    def test_keeps_genuine_rewrites(self) -> None:
        # Short but a real restyle: nothing of the original's surface survives.
        self.assertEqual(
            providers.clean_generated("I am on my way.", "omw"), "I am on my way.")
        self.assertEqual(
            providers.clean_generated(
                "The building had three floors and many rooms.",
                "dude yes it had like three floors and hella rooms"),
            "The building had three floors and many rooms.")
        # Short all-caps is an initialism, not shouting.
        self.assertEqual(providers.clean_generated("BART is fine.", "bart good"), "BART is fine.")


class TestChatPairs(unittest.TestCase):
    """Conversational (context -> reply) pairs, built from real threads."""

    THREAD = [
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
        turns = build_chat_pairs.to_turns(self.THREAD)
        self.assertEqual(turns[0]["text"], "yo\nu up")
        self.assertEqual(turns[1]["text"], "ya\nbarely")

    def test_pairs_carry_context_and_persona(self) -> None:
        pairs, _ = build_chat_pairs.build_pairs(self.THREAD, my_name="Cyrus")
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertEqual(pair["output"], "ya\nbarely")
        self.assertEqual([t["speaker"] for t in pair["context"]], ["Friend 1"])
        # The raw handle never reaches the training text.
        self.assertNotIn("+1415", json.dumps(pair["context"]))
        self.assertEqual(pair["meta"]["with"], "Friend 1")

    def test_session_gap_drops_orphan_openings(self) -> None:
        # "morning" the next day has no usable context, so it is not a pair.
        pairs, stats = build_chat_pairs.build_pairs(self.THREAD, my_name="Cyrus")
        self.assertEqual(stats["no_context"], 1)
        self.assertTrue(all(p["output"] != "morning" for p in pairs))

    def test_contacts_override_aliases(self) -> None:
        pairs, _ = build_chat_pairs.build_pairs(
            self.THREAD, my_name="Cyrus", contacts={"+14155550001": "Mom"})
        self.assertEqual(pairs[0]["meta"]["with"], "Mom")
        self.assertEqual(pairs[0]["context"][0]["speaker"], "Mom")

    def test_chat_messages_shape(self) -> None:
        pairs, _ = build_chat_pairs.build_pairs(self.THREAD, my_name="Cyrus")
        msgs = formatting.messages_for(pairs[0], my_name="Cyrus")
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant"])
        self.assertIn("You are Cyrus", msgs[0]["content"])
        self.assertIn("texting Friend 1", msgs[0]["content"])
        self.assertEqual(msgs[2]["content"], "ya\nbarely")

    def test_masking_works_on_chat_pairs(self) -> None:
        pairs, _ = build_chat_pairs.build_pairs(self.THREAD, my_name="Cyrus")
        ex = formatting.masked_example(pairs[0], FakeTokenizer(), 2048, "Cyrus")
        supervised = [t for t in ex["labels"] if t != formatting.IGNORE_INDEX]
        self.assertTrue(supervised)
        self.assertLess(len(supervised), len(ex["labels"]))


class TestAllProvidersValidated(unittest.TestCase):
    def test_emit_gate_applies_to_every_provider(self) -> None:
        # The Anthropic paths do not clean their own output, so the shared
        # emit() gate is what keeps a pair meaning the same thing regardless of
        # which backend produced it.
        src = Path(__file__).resolve().parents[1] / "src" / "style_ft" / "generate_pairs.py"
        body = src.read_text()
        emit = body[body.index("def emit("): body.index("if args.provider ==")]
        self.assertIn("clean_generated", emit)


class TestCleanMessages(unittest.TestCase):
    """Artifacts found in the real corpus, not hypothetical ones."""

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
