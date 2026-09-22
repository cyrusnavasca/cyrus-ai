# Design notes

Why the pipeline is shaped the way it is, what the data contracts are, and how a
run is judged. `README.md` covers how to run it; this covers why.

---

## 1. The task changed once, and it mattered

The first version of this project framed the problem as **style transfer**: an
LLM rewrote each of my real messages into a flat, style-free version, and the
model was trained to go back the other way. `input` was synthetic, `output` was
real.

It trained, and it worked at what it was asked to do, and what it was asked to
do turned out to be the wrong thing. Three problems, in increasing order of how
much they mattered:

1. **Cost and noise.** Every pair needed an LLM call, and a validator to reject
   the generations that leaked my phrasing back into the input. Roughly a fifth
   were thrown away.
2. **No notion of a reply.** The adapter restyles a sentence. Served as a chat
   model it parrots whatever you type back at you in the right voice, which
   reads as broken rather than as the restyler it is.
3. **The input was fiction.** Nobody hands me a neutral draft to rewrite. The
   task had no downstream use.

The current version trains on **conversations**: `context` is the real preceding
turns of a real thread, `output` is what I actually replied. No LLM in the data
path, no API cost, no synthetic-input noise, and the model has to produce the
content rather than restyle content it was handed.

That reframing also answered, for free, the three open questions the first design
ended on: preceding turns are now the input; consecutive messages are grouped
into one target so the model learns to send bursts; and the task is "draft the
reply", not "rewrite this".

---

## 2. Data contracts

Every stage reads and writes JSONL, and each is a separate process. Schemas are
additive - later stages ignore unknown fields - so extra columns never break a
downstream step.

**Message** (`ingest.imessage_db`, `ingest.parse_export`)

```json
{"sender": "Me", "text": "...", "timestamp": "2026-01-02T03:04:05+00:00",
 "thread_id": "some group chat", "direction": "out",
 "service": "iMessage", "chat_guid": "...", "participants": ["+1555..."],
 "is_group": true}
```

`direction` is `out` / `in` / `unknown`. `participants` and `chat_guid` are
present only from `imessage_db`.

**Training pair** (`corpus.chat_pairs`)

```json
{"id": "c3865a98f28f8b6a",
 "context": [{"speaker": "Friend 1", "text": "the first and the last right 🌚😳"}],
 "output": "i reset again\ncan u see message",
 "meta": {"thread_id": "...", "with": "Friend 1", "is_group": false,
          "timestamp": "..."}}
```

`id` is content-addressed, which is what makes the train/test split stable as the
corpus grows: adding data never reshuffles what was already held out.

Speakers are pseudonymized (`Friend 3`) unless a contacts map supplies a name.
Raw handles are noise the model would memorize instead of a person, and they
would end up in the weights of a model that gets served publicly.

**Predictions** (`evaluation.runner generate`)

```json
{"id": "...", "input": "<rendered transcript>", "reference": "<real reply>",
 "base_output": "...", "tuned_output": "..."}
```

---

## 3. Decisions worth defending

**Turns, not messages.** People text in bursts - "wait", "actually", "nvm". A
model trained on one fragment at a time learns to stop mid-thought, so
consecutive messages from the same speaker within `--burst-seconds` are joined
into one turn on both sides of the pair.

**Session gaps.** A reply to something said three days ago is a new
conversation, not a reply. Past `--session-gap-minutes` the context is dropped
and the message is not used as a training target.

**No per-thread cap.** `select_threads` can cap a dominant conversation, and for
this dataset it must not be used: it drops a random subset of my messages from
inside threads, which punches holes in the very conversations the context windows
are built from. The largest thread being 58% of the corpus is the intent - the
model should sound like this person with *these* people, not like an average of
everyone.

**Loss on the assistant turn only.** Without masking, the model also learns to
produce the system prompt and the conversation that preceded the reply, neither
of which it is ever asked to generate. The mask boundary is derived from the
tokenizer's own chat template rather than hardcoded role markers, so it stays
correct across base models, and a template that does not render the prompt as a
prefix raises rather than silently falling back to training on everything.

**Overlong examples are dropped, not truncated.** Truncating cuts the reply, not
the prompt: it can leave an example with every label masked (a NaN loss for a
batch of them) and it strips the trailing EOS, teaching the model never to stop.

**Emoji, one-word messages and shouting are kept.** They are 9% of this corpus
and they are the style, not noise. 35% of training targets contain an emoji;
😭 and 💀 alone are 41% of all emoji used.

---

## 4. Training configuration

| Parameter | Default | Rationale |
| --- | --- | --- |
| Base model | `unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit` | Best-documented Unsloth path; fits 16GB in 4-bit at seq 2048 |
| LoRA rank | 16 | Style is a low-rank change; 8 often suffices, 32 starts memorizing |
| LoRA alpha | 16 | `alpha == r` is the stable Unsloth recommendation |
| Dropout | 0.0 | Unsloth's fast path; regularize with fewer epochs instead |
| Target modules | all attention + MLP projections | Style shows up in both; attention-only underfits |
| Learning rate | 2e-4 | Standard for rank-16 LoRA at this batch size |
| Epochs | 1 | 48k pairs; at 2-3 epochs this memorizes and costs ~5 GPU-hours |
| Effective batch | 16 (2 × 8 accum) | Smoother loss on a corpus this size |
| Max seq length | 2048 | Far above need - a six-turn context is rarely over 600 chars |
| Seed | 3407 | Fixed for reproducibility |

**Sweep order if a run underperforms**, cheapest first: epochs, then rank, then
context-turn count, then base model. Learning rate is rarely the binding
constraint here.

---

## 5. How a run is judged

Three tiers, cheapest first. None is sufficient alone.

### Tier 1 - surface features (`evaluation.metrics`)

Computed per message and averaged. For each feature the report gives reference /
base / tuned means and marks which model is closer to the reference.

| Metric | Definition | Catches | Blind to |
| --- | --- | --- | --- |
| `chars` | Character count | Verbosity drift - the most common base-model failure | Content quality |
| `words` | Word-token count | Same, length-normalized | — |
| `lowercase_ratio` | lowercase letters ÷ all letters | Sentence-case habit; the loudest single marker | Which words are capitalized |
| `emoji_per_100w` | emoji ÷ words × 100 | Emoji frequency | Emoji *choice* - 😭 and 🙃 score identically |
| `abbrev_ratio` | tokens in a 30-word list ÷ words | `u`, `rn`, `ngl`, `tbh` habits | Abbreviations outside the list |
| `ellipsis_per_100w` | `...` count ÷ words × 100 | Trailing-off punctuation | `..` and `…` variants |
| `exclam_per_100w` | `!` count ÷ words × 100 | Exclamation habit | Runs (`!!!` counts as three) |
| `terminal_punct_ratio` | sentences ending `.!?` ÷ sentences | Dropped end punctuation | — |
| `avg_word_len` | Mean characters per word | Register, roughly | Vocabulary itself |

This tier is a regression signal and a diagnosis aid - it tells you *which*
marker gave the model away. It is not a verdict. A model that lowercases
everything and drops periods scores well while being nonsense.

### Tier 2 - held-out loss (`tools/probe_fit.py`)

Mean per-token negative log-likelihood of held-out real replies, scored on the
assistant tokens only - the same tokens training optimized - for the base model
and the adapter.

Texting is a high-entropy target, and the absolute number is misleading on its
own: a final training loss of ~3.25 looks alarming until you see the base model
at perplexity 120 against the adapter's 20.6 on the same held-out pairs. The gap
to base is the honest reading.

### Tier 3 - blind identification (not built)

The measure that actually answers "does this sound like me": *n* forced-choice
trials, each showing the real reply and the model's attempt in randomized order,
with the answer key written to disk before the first trial.

Sample size matters more than it sounds. Under the null the standard error is
`sqrt(0.25/n)`:

| n | SE | 95% CI half-width | Can it separate 50% from 65%? |
| --- | --- | --- | --- |
| 20 | 11.2% | ±21.9% | No |
| 60 | 6.5% | ±12.6% | Marginal |
| 100 | 5.0% | ±9.8% | Yes |

At n=20 a 65% result spans 43%–87%, which is compatible with both "perfect" and
"obvious". n=60 minimum. Accuracy below 40% is not a triumph - it means the
harness is malformed and the reference side is giving itself away.

---

## 6. Risks this is built to catch

**Short messages carry too little signal.** Median message is 40 characters. At
that length "terse, lowercase, no terminal punctuation" may be the entire
learnable signal, and Tier 1 scores that as success. Caught by Tier 2, which
cannot be gamed by surface mimicry.

**One relationship dominates.** Deliberate here, but it means the adapter's
register is specific to those threads. Per-thread evaluation would confirm how
far it generalizes; a sharp quality cliff between threads is the failure signal.

**Memorization masquerading as style.** More epochs over a corpus this size
produce verbatim recall: fluent, well-scoring recitation. Caught by eval loss
turning upward and by test-set-only evaluation.

**The sampler, not the weights.** Early output read as "a random message with no
thought". Most of that was temperature: texting is genuinely high-entropy, so
0.7 samples deep enough into a flat distribution to pick a fluent reply about
something else. 0.5 keeps the voice and stays on topic; 0.35 never wanders but
goes flat. Always rule out the sampler before retraining.

---

## 7. Artifacts per run

Under `outputs/<run>/`:

| File | Produced by | Contents |
| --- | --- | --- |
| `training_config.json` | `train` | Base model, data, hyperparameters, final loss, format |
| `predictions.jsonl` | `eval generate` | Reference / base / tuned per held-out pair |
| `eval_report.md` | `eval report` | Side-by-side plus the Tier 1 table |
| `eval_report.csv` | `eval report` | Same data, spreadsheet-shaped |
| `eval_metrics.json` | `eval report` | Tier 1 values, machine-readable |

One directory per run makes runs comparable after the fact.
