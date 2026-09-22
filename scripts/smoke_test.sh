#!/usr/bin/env bash
# End-to-end run of every stage that does not need a GPU, on synthetic data.
#
# This is the proof that the pipeline works without touching a real message
# history: the same commands, the same file contracts, 40 fake messages instead
# of 400,000 real ones. Training is the only step it cannot cover; the command
# for that is printed at the end.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="src:${PYTHONPATH:-}"

D=data/dummy
O=outputs/smoke
rm -rf "$O"

run() { echo; echo "== $*"; }

run "0. unit tests"
python3 -m unittest discover -t . -s tests

run "1. synthetic export files"
python3 -m style_ft.cli dummy-export --outdir "$D"

run "2a. ingest (csv)"
python3 -m style_ft.cli export --input "$D/export.csv" --output "$D/messages.jsonl"

run "2b. ingest (xml) - must agree with csv"
python3 -m style_ft.cli export --input "$D/export.xml" --output "$D/messages_xml.jsonl"
python3 - <<'PY'
import json, pathlib
a = [json.loads(l)["text"] for l in pathlib.Path("data/dummy/messages.jsonl").read_text().splitlines()]
b = [json.loads(l)["text"] for l in pathlib.Path("data/dummy/messages_xml.jsonl").read_text().splitlines()]
assert a == b, "csv and xml parsers disagree"
print("csv/xml parsers agree on %d messages" % len(a))
PY

run "3. clean the corpus"
python3 -m style_ft.cli clean --input "$D/messages.jsonl" --output "$D/clean.jsonl"

run "4. build (context -> my reply) pairs"
python3 -m style_ft.cli pairs --input "$D/clean.jsonl" --output "$D/chat_pairs.jsonl" \
    --my-name Me --context-turns 4

run "5. train/test split"
python3 -m style_ft.cli split --input "$D/chat_pairs.jsonl" \
    --train "$D/chat_train.jsonl" --test "$D/chat_test.jsonl" --test-frac 0.2

run "5b. split is stable across reruns"
before=$(sha1sum < "$D/chat_train.jsonl" 2>/dev/null || shasum < "$D/chat_train.jsonl")
python3 -m style_ft.cli split --input "$D/chat_pairs.jsonl" \
    --train "$D/chat_train.jsonl" --test "$D/chat_test.jsonl" --test-frac 0.2
after=$(sha1sum < "$D/chat_train.jsonl" 2>/dev/null || shasum < "$D/chat_train.jsonl")
[ "$before" = "$after" ] || { echo "split is not deterministic"; exit 1; }
echo "split stable"

run "6. eval report (fabricated predictions, no GPU)"
python3 tools/fake_predictions.py --test "$D/chat_test.jsonl" --out "$O/predictions.jsonl"
python3 -m style_ft.cli eval report --predictions "$O/predictions.jsonl" --outdir "$O"

echo
echo "SMOKE TEST PASSED"
echo "  pairs:  $D/chat_pairs.jsonl"
echo "  report: $O/eval_report.md"
echo
echo "The training step still needs a CUDA box:"
echo "  style-ft train --train $D/chat_train.jsonl --output-dir $O/lora \\"
echo "      --base-model unsloth/Qwen2.5-0.5B-Instruct --max-steps 5"
