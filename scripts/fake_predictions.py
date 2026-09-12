"""Fabricate a predictions file so the report half of the eval harness can be
exercised without a GPU. base_output = a bland rewrite, tuned_output = the
reference. Real predictions come from `python -m style_ft.evaluate generate`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from style_ft.jsonlio import read_jsonl, write_jsonl  # noqa: E402
from style_ft.providers import dummy_neutralize  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--test", default="data/dummy/test.jsonl")
ap.add_argument("--out", default="outputs/smoke/predictions.jsonl")
args = ap.parse_args()

rows = [
    {
        "id": p.get("id", ""),
        "input": p["input"],
        "reference": p["output"],
        "base_output": dummy_neutralize(p["output"]),
        "tuned_output": p["output"],
    }
    for p in read_jsonl(args.test)
]
n = write_jsonl(args.out, rows)
print(f"wrote {n} fake predictions -> {args.out}")
