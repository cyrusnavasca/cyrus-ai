"""Prompt rendering and loss masking.

The mask is the part of training most likely to be silently wrong: get it
backwards and the run still completes, still reports a falling loss, and still
produces an adapter - one that has been taught to emit the prompt.
"""

from __future__ import annotations

import unittest

from style_ft.corpus import chat_pairs
from style_ft.modeling import formatting


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


PAIR = {
    "context": [{"speaker": "Friend 1", "text": "yo u up"}],
    "output": "ya\nbarely",
    "meta": {"with": "Friend 1", "is_group": False},
}


class TestFormatting(unittest.TestCase):
    def test_roles_and_persona(self) -> None:
        msgs = formatting.messages_for(PAIR, my_name="Cyrus")
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant"])
        self.assertIn("You are Cyrus", msgs[0]["content"])
        self.assertEqual(msgs[1]["content"], "Friend 1: yo u up")
        self.assertEqual(msgs[2]["content"], "ya\nbarely")

    def test_inference_prompt_drops_the_reply(self) -> None:
        self.assertEqual(len(formatting.messages_for(PAIR, include_response=False)), 2)

    def test_group_chats_get_their_own_system_prompt(self) -> None:
        group = {**PAIR, "meta": {"with": "o block", "is_group": True}}
        self.assertIn('group chat "o block"', formatting.chat_system(group, "Cyrus"))


class TestLossMasking(unittest.TestCase):
    def test_only_the_assistant_turn_is_supervised(self) -> None:
        ex = formatting.masked_example(PAIR, FakeTokenizer(), 2048, "Cyrus")
        self.assertEqual(len(ex["input_ids"]), len(ex["labels"]))
        self.assertEqual(len(ex["attention_mask"]), len(ex["labels"]))

        supervised = [t for t in ex["labels"] if t != formatting.IGNORE_INDEX]
        self.assertTrue(supervised, "nothing is supervised - the mask ate everything")
        # The supervised span is the tail, and it is the assistant turn only.
        self.assertEqual(supervised, ex["input_ids"][-len(supervised):])
        self.assertLess(len(supervised), len(ex["labels"]), "prompt tokens are not masked")

        tok = FakeTokenizer()
        n_prompt = len(tok(tok.apply_chat_template(
            formatting.messages_for(PAIR, include_response=False, my_name="Cyrus"),
            add_generation_prompt=True))["input_ids"])
        self.assertEqual(ex["labels"][:n_prompt], [formatting.IGNORE_INDEX] * n_prompt)

    def test_overlong_examples_are_dropped_not_truncated(self) -> None:
        # Truncating cuts the reply, not the prompt: it can mask every label
        # (NaN loss) and strips the EOS, teaching the model never to stop.
        self.assertIsNone(formatting.masked_example(PAIR, FakeTokenizer(), 4))

    def test_raises_when_prompt_is_not_a_prefix(self) -> None:
        with self.assertRaises(formatting.PromptNotAPrefixError):
            formatting.masked_example(PAIR, FakeTokenizer(prompt_is_prefix=False), 2048)

    def test_masking_works_on_freshly_built_pairs(self) -> None:
        # Guards the seam between the two modules: a pair straight out of the
        # corpus stage must be maskable without any massaging in between.
        pairs, _ = chat_pairs.build_pairs(
            [
                {"text": "yo", "direction": "in", "sender": "+1555", "thread_id": "t",
                 "timestamp": "2026-01-01T10:00:00+00:00", "is_group": False},
                {"text": "ya", "direction": "out", "sender": "Me", "thread_id": "t",
                 "timestamp": "2026-01-01T10:01:00+00:00", "is_group": False},
            ],
            my_name="Cyrus",
        )
        ex = formatting.masked_example(pairs[0], FakeTokenizer(), 2048, "Cyrus")
        supervised = [t for t in ex["labels"] if t != formatting.IGNORE_INDEX]
        self.assertTrue(supervised)
        self.assertLess(len(supervised), len(ex["labels"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
