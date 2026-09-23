# style-ft

Fine-tune an open LLM on a personal iMessage history so it replies the way I
actually text — same voice, same length, same punctuation habits, same emoji.

412,000 messages out of the macOS `chat.db` become 48,000 conversational
training pairs, a LoRA adapter over Llama 3.1 8B trained on a rented A100, and a
token-gated chat page you can text.

```
them:  she texted me back after 3 weeks
CYRUS: LMAOAOA

them:  and said 'hey stranger'
CYRUS: 😭💀
       dude i have a better idea of how to make you laugh than her 😩

them:  what do i even say to that
CYRUS: say "hmm interesting" then send pic of your butt
```

Everything except the fine-tune itself runs on a laptop, with no GPU, no API key
and no third-party dependencies — the corpus pipeline is pure stdlib. The whole
path is proven end to end on synthetic data: `bash scripts/smoke_test.sh`.

---

## The pipeline

```
macOS chat.db                    OR   an export (csv / xml / json)
   │  style-ft imessage                │  style-ft export
   └──────────────┬────────────────────┘
                  ▼   data/interim/messages.jsonl   {sender, text, timestamp, thread_id, direction}
   │  style-ft threads    pick the conversations that are actually your voice (--list to browse)
   ▼
   │  style-ft clean      drop attachment rows, apply chat.db edit records, kill pasted text
   ▼
   │  style-ft pairs      real preceding turns -> what you actually replied
   │  style-ft split      content-addressed, stable as the corpus grows
   ▼   data/processed/chat_{train,test}.jsonl
   │  style-ft train      Unsloth LoRA -> outputs/<run>/adapter              [CUDA GPU]
   ▼
   │  style-ft eval generate   base vs. tuned on held-out pairs              [CUDA GPU]
   │  style-ft eval report     side-by-side + surface-style metrics          [anywhere]
```

Each stage is its own process with a JSONL file between it and the next. Each is
slow, each is worth reading before running the next, and a pipeline you can stop
halfway is the one you can actually debug.

## Repository layout

```
src/style_ft/
  ingest/       chat.db reader, export parsers        -> one message record shape
  corpus/       thread selection, cleaning, pairing, train/test split
  modeling/     prompt rendering (shared by train and inference), LoRA training
  evaluation/   held-out generation, surface-style metrics, report rendering
  common/       JSONL I/O, deterministic ranking
  cli.py        `style-ft <stage>`
deploy/         Modal apps: train.py rents the GPU, serve.py serves the chat page
tools/          one-off diagnostics (underfit probe, collator probe, fake predictions)
scripts/        setup, corpus rebuild, end-to-end smoke test
.env.example    thread curation, persona name, serving token
tests/          50 unit tests; no network, no GPU, no fixtures larger than a screen
```

## Quickstart

```bash
bash scripts/setup_local.sh     # venv + editable install
bash scripts/smoke_test.sh      # the whole pipeline on 40 synthetic messages
```

Then, on your own history:

```bash
# 1. read the database (needs Full Disk Access, see below)
style-ft imessage --output data/interim/messages.jsonl

# 2. see who you actually talk to, then curate
style-ft threads --input data/interim/messages.jsonl --list
cp .env.example .env               # MY_NAME, THREADS_INCLUDE, THREADS_EXCLUDE
bash scripts/build_dataset.sh      # threads -> clean -> pairs -> split

# 3. train on a rented A100 and serve the result
modal volume put --force style-ft data/processed/chat_train.jsonl /chat_train.jsonl
modal volume put --force style-ft data/processed/chat_test.jsonl  /chat_test.jsonl
modal run --detach deploy/train.py --step train --run-name chat-v3 --my-name Cyrus --no-wait
modal deploy deploy/serve.py
```

Every stage takes `--help`.

---

## Notes per stage

**Reading chat.db.** `style-ft imessage` is the best source on macOS: `is_from_me`
is authoritative rather than guessed from a sender column, timestamps are exact,
group vs. 1:1 is known (`--dms-only`), and tapbacks, system events and app
payloads are excluded by query rather than by string matching. Messages written
by recent OS versions leave `message.text` NULL and store the body in
`attributedBody`; that blob is decoded too, so those messages are not silently
lost. The database is copied to a temp directory before reading, so the live one
is never touched.

Requires **Full Disk Access** for whatever app runs the command (System Settings
→ Privacy & Security → Full Disk Access), then a full quit and reopen of that
app. Without it the read fails with `operation not permitted` / `no such table:
message`.

**Parsing exports.** iMazing CSV, SMS Backup & Restore XML, and JSON/JSONL are
auto-detected by extension. Column names are fuzzy-matched; override with
`--column-map '{"text": ["MyColumn"]}'`. XML is streamed, so a multi-GB backup
does not need to fit in memory. (`imessage-exporter` 4.2.0 only emits `txt` and
`html`, which is the other reason this reads the database directly.)

**Choosing conversations.** `style-ft threads --list` ranks every thread by how
many messages you wrote in it. Curation here is the highest-leverage step in the
project: family threads have their own register ("son", "thanks mom"), club and
coursework threads are logistics rather than voice, and the long tail is
acquaintances and automated messages — an early eval example turned out to be an
auto-reply whose reference answer was the single word "urgent".

**Cleaning.** chat.db writes an edited message as a *second* row (`Edited to
"…"`), so the corpus holds both the typo and the fix; the edit is applied to the
message it corrects rather than dropped. Attachment placeholders, pasted lyrics
and homework, consecutive duplicates and bare links go. Emoji-only and one-word
messages stay — they are 9% of this corpus and they are the style, not noise.

**Building pairs.** For each of my replies, the real preceding turns become the
input. Consecutive messages from one speaker inside `--burst-seconds` are joined
into a single turn, because people text in bursts and a model trained on one
fragment at a time learns to stop mid-thought. Past `--session-gap-minutes` the
context is dropped: a reply to something said three days ago is a new
conversation. Speakers are pseudonymized unless `--contacts` supplies names.

**Training.** Unsloth LoRA, r=16 / alpha=16 / lr=2e-4, one epoch over 48k pairs.
Loss is computed on the assistant turn only, with the mask boundary derived from
the tokenizer's own chat template rather than hardcoded role markers. Examples
that would not fit are dropped rather than truncated — truncation cuts the reply,
not the prompt, which strips the EOS and teaches the model never to stop.

**Prompt consistency.** `src/style_ft/modeling/formatting.py` builds the chat
messages for both training and inference. Nothing else may build them: a mismatch
does not raise, it just quietly degrades the fine-tune.

**Eval.** `generate` needs the GPU; `report` runs anywhere off
`predictions.jsonl`, so reports are regenerated locally weeks later. The report
gives a reference / base / tuned side-by-side plus surface-style metrics
(lowercase ratio, abbreviation rate, emoji rate, terminal punctuation, length).
Those metrics are a regression signal, not a verdict — read the side-by-side.
`docs/design.md` has the full metric definitions and the decision rule.

**Serving.** `deploy/serve.py` is a FastAPI app plus a single-file chat page.
`STYLE_FT_TOKEN` is required and has no default — a fallback committed to a
public repo is a published password, not a gate. Two Modal functions on purpose:
Modal caps a web request at 150 seconds and then answers with a 303 to a result
URL, which a browser fetch cannot follow across
origins. Loading an 8B model on a cold container can exceed that, so the page
spawns the work and polls for it. Access is token-gated — the model writes in one
person's voice and was trained on their friends' messages.

---

## What this cost to get right

Things that were wrong first, and are in the code as comments so they stay fixed:

- **Sampling temperature, not the weights.** Early replies read as "a fluent
  message about something else". Texting is high-entropy, so 0.7 samples deep
  into a flat distribution; 0.5 keeps the voice on topic and, counter-intuitively,
  yields *more* emoji, because the mode moves toward them.
- **The persona prompt named the most-trained-on contact.** The adapter then
  wrote to that person regardless of who was actually texting.
- **Per-thread capping punched holes in conversations.** It drops a random
  subset of *your* messages from inside threads — fine for single-message
  training, fatal once the input is the surrounding turns.
- **Held-out pairs were taken from the head of the file.** The test file is
  grouped by thread, so `pairs[:25]` is one conversation with one person, which
  is how an early report came to describe a single contact as the model's
  overall behaviour.
- **A first attempt at input hygiene lowercased ALL-CAPS messages,** deleting a
  register that is genuinely part of the voice.

## Privacy

`data/` is gitignored except the synthetic export fixtures the smoke test runs
on, and no message has ever been committed — `git log` over `data/` turns up
three empty `.gitkeep` files and nothing else.

Anything naming a real person lives in `.env`, which is gitignored: the curated
thread list, the persona name, the serving token. `.env.example` is the
committed copy, with placeholder numbers.

Real message content never leaves the machine. The corpus pipeline makes no
network calls at all — it has no third-party dependencies to make them with —
and the only thing uploaded is the finished training set, to your own Modal
volume.

## Tests

```bash
python -m unittest discover -t . -s tests -v   # 50 tests, no GPU, no network
ruff check .
bash scripts/smoke_test.sh                     # the pipeline end to end
```

CI runs all three on 3.10 and 3.12.

## License

MIT — see [LICENSE](LICENSE).
