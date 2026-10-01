"""Guards on the public chat endpoint: what a stranger may send, what the model may say back.

The Vercel proxy applies the same input limits, but anyone who finds the Modal
URL can skip the proxy, so these run on the Modal side too and are tested here
on their own. The redaction cases include things that look like numbers but are
not phone numbers - a texting corpus is full of times, prices and years, and a
scrub that eats "9:41" would wreck the voice it is meant to protect.
"""

from __future__ import annotations

import unittest

from style_ft.serving.guard import (
    MAX_CHARS,
    REDACTED,
    TEMPERATURE_DEFAULT,
    clamp_temperature,
    clean_history,
    pick_partner,
    redact,
)


class CleanHistoryTest(unittest.TestCase):
    def test_keeps_only_the_last_context_turns(self):
        history = [{"me": i % 2 == 1, "text": f"m{i}"} for i in range(10)]
        out = clean_history(history)
        self.assertEqual([t["text"] for t in out], [f"m{i}" for i in range(4, 10)])

    def test_strips_and_clamps_each_turn(self):
        out = clean_history([{"me": False, "text": "  " + "x" * 280 + " "},
                             {"me": True, "text": " hi "}])
        self.assertEqual(out[0]["text"], "x" * 280)
        self.assertEqual(out[1], {"me": True, "text": "hi"})
        out = clean_history([{"me": False, "text": "y" * 350}, {"me": True, "text": "ok"}])
        self.assertEqual(len(out[0]["text"]), MAX_CHARS)

    def test_coerces_me_to_bool(self):
        out = clean_history([{"text": "a"}, {"me": 1, "text": "b"}])
        self.assertEqual([t["me"] for t in out], [False, True])

    def test_rejects_total_over_limit_before_clamping(self):
        # 6 x 400 raw chars = 2400 > 2000. Clamping first would hide this,
        # since 6 x 300 = 1800 always fits.
        with self.assertRaises(ValueError):
            clean_history([{"me": True, "text": "x" * 400}] * 6)

    def test_total_counts_only_the_recent_turns(self):
        history = [{"me": True, "text": "x" * 300}] * 10
        self.assertEqual(len(clean_history(history)), 6)

    def test_rejects_bad_shapes(self):
        for bad in (None, [], "hi", 5, [5], [{"me": True, "text": 5}], {"history": []}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                clean_history(bad)

    def test_rejects_an_empty_newest_message(self):
        with self.assertRaises(ValueError):
            clean_history([{"me": False, "text": "hey"}, {"me": True, "text": "   "}])


class TemperatureTest(unittest.TestCase):
    def test_default_for_missing_or_junk(self):
        for value in (None, "", "hot", float("nan"), float("inf"), [0.5]):
            with self.subTest(value=value):
                self.assertEqual(clamp_temperature(value), TEMPERATURE_DEFAULT)

    def test_clamps_into_range(self):
        self.assertEqual(clamp_temperature(0), 0.3)
        self.assertEqual(clamp_temperature(5), 0.8)
        self.assertEqual(clamp_temperature("0.6"), 0.6)
        self.assertEqual(clamp_temperature(0.5), 0.5)


class PartnerTest(unittest.TestCase):
    def test_only_allowed_names_pass(self):
        allowed = ("Friend 1",)
        self.assertEqual(pick_partner("Friend 1", allowed, "Friend 1"), "Friend 1")
        self.assertEqual(pick_partner("Friend 7", allowed, "Friend 1"), "Friend 1")
        self.assertEqual(pick_partner(None, allowed, "Friend 1"), "Friend 1")
        self.assertEqual(pick_partner(["Friend 1"], allowed, "Friend 1"), "Friend 1")


class RedactTest(unittest.TestCase):
    def test_scrubs_phone_numbers(self):
        cases = {
            "call me 415-555-0123": f"call me {REDACTED}",
            "+1 (415) 555-0123": REDACTED,
            "4155550123 is mine": f"{REDACTED} is mine",
            "415.555.0123": REDACTED,
            "uk is +44 20 7946 0958": f"uk is {REDACTED}",
            "just 555-0123": f"just {REDACTED}",
            "415-555-0123 415-555-0199": REDACTED,
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(redact(text), want)

    def test_scrubs_emails(self):
        self.assertEqual(redact("its a.b+c@gmail.com ok"), f"its {REDACTED} ok")
        self.assertEqual(redact("x_y@uni.edu."), f"{REDACTED}.")

    def test_leaves_ordinary_numbers_alone(self):
        for text in ("meet at 9:41", "it was $12.50", "class of 2024", "2019-2024 was wild",
                     "10/01/2026", "lol 100", "i owe you 1,000,000", "1234567", "😭💀", ""):
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)


if __name__ == "__main__":
    unittest.main()
