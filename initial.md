# Style Transfer Fine-Tuning — Project Setup Tasks

Goal: Fine-tune an open LLM (via LoRA/Unsloth) to write in my personal texting/writing style.
This file covers everything that can be set up BEFORE the real data (text message export) is ready.

**Status:** scaffold built and proven end-to-end on synthetic data (`bash scripts/smoke_test.sh`,
20 unit tests passing). See `README.md`. Only the GPU-dependent items are still open — this
machine has MPS, not CUDA, and Unsloth has no Apple Silicon build.

---

## 1. Environment Setup

- [x] Create a Python venv or conda env for this project — `scripts/setup_local.sh` (no GPU),
      `scripts/setup_gpu.sh` (CUDA box)
- [x] Install: `unsloth`, `transformers`, `peft`, `trl`, `datasets`, `bitsandbytes`, `torch` —
      pinned in `requirements-train.txt`; `requirements.txt` holds the GPU-free core (`anthropic` only)
- [ ] **Confirm GPU access** — `scripts/setup_gpu.sh` prints device/VRAM and warns if CUDA is absent.
      Blocked here: this machine reports `cuda False`, `mps True`. Needs Colab or a rented GPU.
- [ ] **Run a tiny smoke-test fine-tune on dummy data** — command is ready, needs the CUDA box:
      `python -m style_ft.train_lora --train data/dummy/train.jsonl --output-dir outputs/smoke/lora \
       --base-model unsloth/Qwen2.5-0.5B-Instruct --max-steps 5`
- [x] Pick base model — default `unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit` (4-bit, fits 16GB at
      seq 2048). Swap with `--base-model`; use `unsloth/Qwen2.5-0.5B-Instruct` for smoke tests.

## 2. Data Pipeline Skeleton

- [x] Parser script — `style_ft.parse_export`. iMazing CSV, SMS Backup & Restore XML (streamed),
      and JSON/JSONL. Fuzzy column matching, `--column-map` override. Emits
      `{"sender", "text", "timestamp", "thread_id", "direction"}`
- [x] Filter step: outgoing only — `style_ft.filter_messages`, via the parsed `direction`,
      `--sender-name`, or `--assume-all-mine`
- [x] Dedup / low-signal filter — configurable `--min-chars` / `--min-words` / `--max-chars`,
      a 50-entry stoplist (`--stoplist-file`, `--extra-stopwords`), emoji-only, URL-only and
      attachment-placeholder dropping, normalized dedup. Prints per-reason stats
- [x] Output: clean list of candidate style samples

## 3. Pair-Generation Scaffold

- [x] Script generating a synthetic input from each real message — `style_ft.generate_pairs`,
      Claude Messages API or Batches API (`--batch`, 50% cheaper). Resumable; refusals skipped
- [x] Prompt template defined and documented — `src/style_ft/prompts.py`, three input styles
      (`neutral` / `bullet` / `topic`), rules explained in the module docstring and README
- [x] Tested on dummy messages — offline `--provider dummy` backend, run by the smoke test
- [x] Train/test split — `style_ft.split_dataset`, 80/20 default, `--test-frac`. Hash-based, so
      assignments are stable as the dataset grows (no sample ever migrates test → train)

## 4. Training Script

- [x] Unsloth LoRA script parameterized by dataset path — `style_ft.train_lora`
- [x] LoRA hyperparameters with sane defaults — r=16, alpha=16, dropout=0, lr=2e-4, 3 epochs,
      batch 2 × grad-accum 4, all overridable
- [ ] **Test end-to-end on the dummy dataset** — blocked on CUDA (see step 1)
- [x] Save checkpoints/adapter weights to a named output directory — `outputs/<run>/adapter`
      plus `training_config.json` recording the base model, data and hyperparameters

## 5. Eval Harness

- [x] Load fine-tuned model, run held-out test inputs — `style_ft.evaluate generate`
- [x] Base vs fine-tuned side by side — `style_ft.evaluate report` (runs with no GPU)
- [x] Write results to file — `eval_report.md`, `eval_report.csv`, `eval_metrics.json`
      (lowercase ratio, abbreviation rate, emoji rate, terminal punctuation, length)

---

## Once real data (text export) is ready:

1. Drop export file into `data/raw/`, run `style_ft.parse_export` + `style_ft.filter_messages`
2. Run `style_ft.generate_pairs --provider anthropic --batch`, then `style_ft.split_dataset`
3. Kick off `style_ft.train_lora` on the GPU box
4. Run `style_ft.evaluate generate` + `report`, judge against these success criteria:
   Drafted from a frequency scan of the 5,000 sampled messages (percentages are how
   often each shows up in your real outgoing texts). Confirm or edit these — they are
   measured from the corpus, not from knowing you.

   - [ ] **No terminal punctuation** (94.4% of your messages). A trailing period reads
         as someone else. The metric catches this one; it is listed because it is the
         single strongest marker.
   - [ ] **`u` / `ur` for you/your** (28.0%) but not uniformly — you also write "you"
         in the same breath. A model that abbreviates *every* time has overshot.
   - [ ] **Emoji as punctuation, not decoration** (24.9%). 😭 and 😐 land where a period
         would, usually after the point, often doubled (😐😐, 😩😩😩😩). Emoji placed
         mid-sentence or used to illustrate a noun is wrong.
   - [ ] **ALL-CAPS for emphasis on whole clauses** (2.6%) — "ITS ALWAYS COLD DUDE
         LMFAO", not scattered capitalised words. Rare but distinctive; v1 lost it
         entirely, which is what the de-shouting change exists to fix.
   - [ ] **Reaction openers** (2.3%) — "oh no", "wait", "damn" before the actual content,
         and "tho" trailing a question (3.3%).

   Also worth watching, not a style marker: **content fidelity on short inputs**. v1
   turned "I need to pay back because I used credit" into "gotta pay back because i
   stole credit". Style is worthless if the message no longer means what it meant.

## Open decisions

- Base model: Llama 3.1 8B assumed. Mistral 7B or Qwen2.5 7B work with `--base-model` alone.
- `--input-style`: `neutral` assumed. Pick based on how you want to prompt it later
  (rewrite-my-draft vs expand-my-bullets).
- Dataset size: below ~300 pairs, LoRA style transfer tends to be weak. The filter stats print
  the yield — if it is low, loosen `--min-chars` / `--min-words` first.
