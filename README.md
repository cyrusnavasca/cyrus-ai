# Style Transfer Fine-Tuning

Fine-tune an open LLM (LoRA via Unsloth) to write in my personal texting style.

Everything except the training step itself runs with no GPU and no API key.
The whole pipeline is proven end to end on synthetic data — `bash scripts/smoke_test.sh`.

## Pipeline

```
macOS Messages chat.db          OR   raw export (csv/xml/json)
   │  style_ft.imessage_db             │  style_ft.parse_export
   └──────────────┬────────────────────┘
                  ▼                    → data/interim/messages.jsonl
   │  style_ft.select_threads      → pick/cap conversations (--list to browse)
   │                                 {sender, text, timestamp, thread_id, direction}
   ▼
   │  style_ft.filter_messages     → data/processed/my_messages.jsonl
   │                                 outgoing only, low-signal dropped, deduped
   ▼
   │  style_ft.generate_pairs      → data/processed/pairs.jsonl
   │                                 {id, input (synthetic), output (real message)}
   │  style_ft.split_dataset       → train.jsonl / test.jsonl
   ▼
   │  style_ft.train_lora          → outputs/<run>/adapter            [CUDA GPU]
   ▼
   │  style_ft.evaluate generate   → outputs/<run>/predictions.jsonl  [CUDA GPU]
   │  style_ft.evaluate report     → eval_report.md / .csv / metrics.json
```

## Setup

```bash
bash scripts/setup_local.sh      # this machine: parsing, filtering, pair-gen, reporting
bash scripts/smoke_test.sh       # end-to-end proof on synthetic data
```

Training needs CUDA (Unsloth has no Apple Silicon build). On a Colab / rented GPU box:

```bash
bash scripts/setup_gpu.sh
```

## Running it for real

```bash
export PYTHONPATH=src
export ANTHROPIC_API_KEY=...     # only needed for step 3

# 1. get the messages — straight from the Mac Messages database (preferred)
python -m style_ft.imessage_db --output data/interim/messages.jsonl
#    ...or from a file export
python -m style_ft.parse_export --input data/raw/export.csv --output data/interim/messages.jsonl

# 2. keep only my messages, drop noise
python -m style_ft.filter_messages \
    --input data/interim/messages.jsonl \
    --output data/processed/my_messages.jsonl \
    --min-chars 25 --min-words 5

# 3. generate synthetic inputs (Batches API: 50% cheaper, async, best for 1000s)
python -m style_ft.generate_pairs \
    --input data/processed/my_messages.jsonl \
    --output data/processed/pairs.jsonl \
    --provider anthropic --batch

python -m style_ft.split_dataset --input data/processed/pairs.jsonl \
    --train data/processed/train.jsonl --test data/processed/test.jsonl --test-frac 0.2

# 4. train (GPU box)
python -m style_ft.train_lora --train data/processed/train.jsonl \
    --eval data/processed/test.jsonl --output-dir outputs/llama3-style-v1

# 5. eval
python -m style_ft.evaluate generate --test data/processed/test.jsonl \
    --adapter outputs/llama3-style-v1/adapter --out outputs/llama3-style-v1/predictions.jsonl
python -m style_ft.evaluate report --predictions outputs/llama3-style-v1/predictions.jsonl \
    --outdir outputs/llama3-style-v1
```

Every script takes `--help`.

## Notes per step

**Reading chat.db.** `style_ft.imessage_db` is the best source on macOS: `is_from_me`
is authoritative (no guessing from a sender column), timestamps are exact, group vs
1:1 is known (`--dms-only`), and tapbacks, system events and app payloads are excluded
by query rather than by string matching. Messages written by recent OS versions leave
`message.text` NULL and store the body in `attributedBody`; that blob is decoded too,
so those messages are not silently lost. The database is copied to a temp dir before
reading, so the live one is never touched. `--since` / `--until` take `YYYY-MM-DD`.

Requires **Full Disk Access** for whatever app runs the command (System Settings →
Privacy & Security → Full Disk Access), then a full quit and reopen of that app.
Without it the read fails with `operation not permitted` / `no such table: message`.

Note: `imessage-exporter` 4.2.0 only emits `txt` and `html` — there is no `csv`
format — which is the other reason this reads the database directly.

**Choosing conversations.** `style_ft.select_threads --list` ranks every thread by
how many messages you wrote in it. One thread usually dominates a real corpus — if a
single person is 40%+ of your outgoing messages, an uncapped fine-tune learns that
relationship's register rather than your general style. `--max-per-thread N` caps any
one conversation (capping only *your* messages, deterministically), `--include` /
`--exclude` take substrings or exact thread ids, and `--kind dm|group` splits by
conversation type.

**Parsing file exports.** iMazing CSV, SMS Backup & Restore XML, and JSON/JSONL are auto-detected
by extension. Column names are fuzzy-matched (case and punctuation insensitive);
override with `--column-map '{"text": ["MyColumn"]}'` if an export is unusual. XML is
streamed, so a multi-GB backup does not need to fit in memory.

**Filtering.** Outgoing detection tries, in order: `--assume-all-mine`, then
`--sender-name <substring>` (repeatable), then the `direction` field the parser
derived. If the parser reports every message as `unknown`, pass `--sender-name`.
Thresholds (`--min-chars`, `--min-words`, `--max-chars`), the low-signal stoplist
(`--stoplist-file`, `--extra-stopwords`), URL-only dropping and dedup are all flags.

**Pair generation.** For each real message (the training *output*), Claude writes a
style-free *input*. Three framings, `--input-style`:

| style | generated input | use when |
| --- | --- | --- |
| `neutral` (default) | flat rewrite of the same content | "restyle this text as me" |
| `bullet` | 1-4 terse bullets | "expand my notes into a message" |
| `topic` | one-line intent description | loosest coupling, riskiest |

The prompt is in `src/style_ft/prompts.py` and is the main quality lever — if the
fine-tune underperforms, tighten that before touching hyperparameters. It forbids
copying distinctive phrasing into the input, which is what would otherwise let the
model cheat by echoing style it was handed rather than learning it.

Runs are **resumable**: results append as they arrive and reruns skip ids already in
the output file. `--batch` prints a batch id; if the process dies mid-poll, resume
with `--collect-batch-id <id>`. Model defaults to `claude-opus-5` at low effort
(the task is simple and high-volume). Refusals and errored batch items are counted
and skipped, not fatal.

**Training.** Unsloth LoRA, defaults r=16 / alpha=16 / lr=2e-4 / 3 epochs, all
flags. `--max-steps 5` with a tiny base model is the smoke-test path. The adapter,
tokenizer and a `training_config.json` (including the base model id, so eval can
find it automatically) land in `--output-dir`.

**Eval.** `generate` needs the GPU; `report` runs anywhere off `predictions.jsonl`,
so reports can be regenerated locally. The report gives a side-by-side
reference / base / fine-tuned table plus surface-style metrics (lowercase ratio,
abbreviation rate, emoji rate, terminal punctuation, length). Those metrics are a
regression signal, not a verdict — read the side-by-side.

**Prompt consistency.** `src/style_ft/formatting.py` builds the chat messages for
both training and inference. Never build them anywhere else; a mismatch between the
two silently degrades style transfer.

## Privacy

`data/` is gitignored except the synthetic dummy set. Real message exports never get
committed. Pair generation does send message text to the Claude API — that is the one
outbound step; skip it with `--provider dummy` if that is not acceptable and write
inputs by hand instead.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
