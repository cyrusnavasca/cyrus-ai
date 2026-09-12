#!/usr/bin/env bash
# End-to-end run of everything that does not need a GPU or an API key.
# Proves the plumbing before the real message export exists.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="src:${PYTHONPATH:-}"

D=data/dummy
O=outputs/smoke
rm -rf "$O"

echo "== 0. unit tests"
python3 -m unittest discover -s tests

echo "== 1. fake export files"
python3 -m style_ft.dummy_data --outdir "$D"

echo "== 2a. parse (csv)"
python3 -m style_ft.parse_export --input "$D/export.csv" --output "$D/messages.jsonl"

echo "== 2a'. parse (xml) — must agree with csv"
python3 -m style_ft.parse_export --input "$D/export.xml" --output "$D/messages_xml.jsonl"
python3 - <<'PY'
import json, pathlib
a = [json.loads(l)["text"] for l in pathlib.Path("data/dummy/messages.jsonl").read_text().splitlines()]
b = [json.loads(l)["text"] for l in pathlib.Path("data/dummy/messages_xml.jsonl").read_text().splitlines()]
assert a == b, "csv and xml parsers disagree"
print("csv/xml parsers agree on %d messages" % len(a))
PY

echo "== 2b. filter"
python3 -m style_ft.filter_messages --input "$D/messages.jsonl" --output "$D/my_messages.jsonl" \
    --stats-out "$O/filter_stats.json"

echo "== 3. pair generation (dummy provider, no API calls)"
python3 -m style_ft.generate_pairs --input "$D/my_messages.jsonl" --output "$D/pairs.jsonl" \
    --provider dummy --overwrite

echo "== 3b. resume check — second run must add nothing"
before=$(wc -l < "$D/pairs.jsonl")
python3 -m style_ft.generate_pairs --input "$D/my_messages.jsonl" --output "$D/pairs.jsonl" --provider dummy
after=$(wc -l < "$D/pairs.jsonl")
[ "$before" -eq "$after" ] || { echo "resume broken: $before -> $after"; exit 1; }
echo "resume ok ($after pairs)"

echo "== 3c. train/test split"
python3 -m style_ft.split_dataset --input "$D/pairs.jsonl" \
    --train "$D/train.jsonl" --test "$D/test.jsonl" --test-frac 0.2

echo "== 5. eval report (fake predictions)"
python3 scripts/fake_predictions.py --test "$D/test.jsonl" --out "$O/predictions.jsonl"
python3 -m style_ft.evaluate report --predictions "$O/predictions.jsonl" --outdir "$O"

echo
echo "SMOKE TEST PASSED"
echo "  pairs:  $D/pairs.jsonl"
echo "  report: $O/eval_report.md"
echo
echo "Step 1 (GPU smoke fine-tune) still needs a CUDA box:"
echo "  python -m style_ft.train_lora --train $D/train.jsonl --output-dir $O/lora \\"
echo "      --base-model unsloth/Qwen2.5-0.5B-Instruct --max-steps 5"
