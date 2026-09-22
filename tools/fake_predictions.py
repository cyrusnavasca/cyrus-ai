"""Fabricate a predictions file so the report half of the eval harness can be
exercised with no GPU.

`base_output` is a deliberately stiff rewrite of the reference (what an untuned
instruct model sounds like); `tuned_output` is the reference itself. That makes
the report's "closer on N/M features" line come out in a known direction, so the
smoke test can assert on it. Real predictions come from `style-ft eval generate`.

  python tools/fake_predictions.py --test data/dummy/chat_test.jsonl \
      --out outputs/smoke/predictions.jsonl
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from style_ft.common.jsonlio import read_jsonl, write_jsonl
from style_ft.modeling.formatting import chat_transcript

EXPANSIONS = {
    "u": "you", "ur": "your", "r": "are", "k": "okay", "ok": "okay",
    "thx": "thanks", "idk": "I do not know", "rn": "right now", "tmrw": "tomorrow",
    "omw": "on my way", "gonna": "going to", "wanna": "want to", "cuz": "because",
    "lmk": "let me know", "nvm": "never mind", "ofc": "of course", "bet": "sounds good",
}
EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]+")


def stiffen(text: str) -> str:
    """Strip the surface style: no emoji, no abbreviations, sentence case."""
    text = EMOJI.sub("", text).replace("\n", " ")
    text = re.sub(r"([!?.])\1+", r"\1", text)
    words = [EXPANSIONS.get(re.sub(r"[^\w]", "", w).lower(), w) for w in text.split()]
    text = re.sub(r"\s+", " ", " ".join(words)).strip()
    if not text:
        return "Understood."
    return text[0].upper() + text[1:] + ("" if text[-1] in ".!?" else ".")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", default="data/dummy/chat_test.jsonl")
    ap.add_argument("--out", default="outputs/smoke/predictions.jsonl")
    args = ap.parse_args(argv)

    rows = [
        {
            "id": pair.get("id", ""),
            "input": chat_transcript(pair["context"]),
            "reference": pair["output"],
            "base_output": stiffen(pair["output"]),
            "tuned_output": pair["output"],
        }
        for pair in read_jsonl(args.test)
    ]
    if not rows:
        raise SystemExit(f"No pairs in {args.test}")
    write_jsonl(args.out, rows)
    print(f"wrote {len(rows)} fake predictions -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
