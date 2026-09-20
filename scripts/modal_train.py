"""Run the GPU-bound steps of the pipeline on Modal.

Two steps, both optional, both writing back to the same Modal Volume:

  generate  pair generation with an open-weights 7B model on a GPU. Much better
            inputs than a 3B model on a laptop, and ~40x faster.
  train     the Unsloth LoRA fine-tune, plus held-out generation for eval.

Usage (from the repo root):

  pip install modal && python3 -m modal setup

  # one-time: push the messages you want pairs for
  modal volume create style-ft
  modal volume put style-ft data/processed/sampled.jsonl /sampled.jsonl

  # step 3 on a GPU (~30 min for 5000 messages on an A100)
  modal run scripts/modal_train.py --step generate

  # step 4 + 5 (~25 min on an A100)
  modal run scripts/modal_train.py --step train

  # pull the results back
  modal volume get style-ft /outputs ./outputs

Already have pairs from the local ollama provider? Skip `generate`, push them
instead:

  modal volume put style-ft data/processed/train.jsonl /train.jsonl
  modal volume put style-ft data/processed/test.jsonl  /test.jsonl
"""

from __future__ import annotations

import pathlib

import modal

REPO = pathlib.Path(__file__).resolve().parents[1]

app = modal.App("style-ft")

# Data and results. Survives between runs, so a crashed train step does not
# cost the generate step.
vol = modal.Volume.from_name("style-ft", create_if_missing=True)
# Base model weights are multi-GB; caching them makes reruns start in seconds.
hf_cache = modal.Volume.from_name("style-ft-hf-cache", create_if_missing=True)

VOLUMES = {"/vol": vol, "/root/.cache/huggingface": hf_cache}

# Same reasoning as the vLLM image below: Unsloth and bitsandbytes compile
# kernels at import time, so the toolkit has to be present.
train_image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .pip_install("unsloth", "unsloth_zoo", "trl", "peft", "datasets", "bitsandbytes", "accelerate")
    .add_local_dir(REPO / "src" / "style_ft", "/root/style_ft")
)

# vLLM needs the CUDA toolkit on the image, not just a GPU: its flashinfer
# backend JIT-compiles kernels at startup and fails with "Could not find nvcc"
# on a slim base. Hence the -devel CUDA image rather than debian_slim.
generate_image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .uv_pip_install("vllm==0.21.0")
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
    .add_local_dir(REPO / "src" / "style_ft", "/root/style_ft")
)

# A100 is the sweet spot here: an H100 finishes sooner but costs more per job,
# and neither step is long enough for the difference to matter.
GPU = "A100"


@app.function(image=generate_image, gpu=GPU, volumes=VOLUMES, timeout=4 * 60 * 60)
def generate(
    model: str = "Qwen/Qwen2.5-7B-Instruct",
    input_style: str = "neutral",
    limit: int = 0,
    test_frac: float = 0.2,
) -> dict[str, int]:
    """Step 3 on a GPU: one synthetic input per real message, then the split."""
    import json
    import sys

    sys.path.insert(0, "/root")
    from style_ft.jsonlio import log
    from style_ft.prompts import SYSTEM_PROMPT, build_user_prompt
    from style_ft.providers import clean_generated
    from vllm import LLM, SamplingParams

    records = [
        json.loads(line)
        for line in pathlib.Path("/vol/sampled.jsonl").read_text().splitlines()
        if line.strip()
    ]
    records = [r for r in records if r.get("text")]
    if limit:
        records = records[:limit]
    log(f"{len(records)} messages to convert")

    llm = LLM(model=model, max_model_len=2048, gpu_memory_utilization=0.90)
    convos = [
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(r["text"], input_style)},
        ]
        for r in records
    ]
    outputs = llm.chat(convos, SamplingParams(temperature=0.2, max_tokens=512))

    # Same validator the local provider uses, so a pair means the same thing
    # whichever backend produced it.
    import hashlib

    pairs, rejected = [], 0
    for rec, out in zip(records, outputs):
        cleaned = clean_generated(out.outputs[0].text, rec["text"], input_style)
        if not cleaned:
            rejected += 1
            continue
        pairs.append(
            {
                "id": hashlib.sha1(rec["text"].encode("utf-8")).hexdigest()[:16],
                "input": cleaned,
                "output": rec["text"],
                "input_style": input_style,
                "meta": {
                    "timestamp": rec.get("timestamp", ""),
                    "thread_id": rec.get("thread_id", ""),
                },
            }
        )
    log(f"{len(pairs)} usable, {rejected} rejected ({rejected / max(1, len(records)):.1%})")

    def dump(path: str, rows: list[dict]) -> None:
        pathlib.Path(path).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        )

    dump("/vol/pairs.jsonl", pairs)

    # Reuse the real splitter rather than reimplementing the hashing - the split
    # has to stay stable across reruns, and two implementations would drift.
    from style_ft.split_dataset import split  # noqa: PLC0415

    train, test = split(pairs, test_frac)
    dump("/vol/train.jsonl", train)
    dump("/vol/test.jsonl", test)
    log(f"{len(train)} train / {len(test)} test")

    vol.commit()
    return {"pairs": len(pairs), "rejected": rejected, "train": len(train), "test": len(test)}


@app.function(image=train_image, gpu=GPU, volumes=VOLUMES, timeout=6 * 60 * 60)
def train(
    run_name: str = "style-v1",
    base_model: str = "unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit",
    epochs: float = 3,
    rank: int = 16,
    max_steps: int = 0,
) -> str:
    """Steps 4 and 5: LoRA fine-tune, then generate on the held-out test set."""
    import sys

    sys.path.insert(0, "/root")
    from style_ft import evaluate, train_lora

    out_dir = f"/vol/outputs/{run_name}"
    argv = [
        "--train", "/vol/train.jsonl",
        "--eval", "/vol/test.jsonl",
        "--output-dir", out_dir,
        "--base-model", base_model,
        "--epochs", str(epochs),
        "--rank", str(rank),
    ]
    if max_steps:
        argv += ["--max-steps", str(max_steps)]
    train_lora.main(argv)
    vol.commit()

    # Generation needs the GPU, so it runs here; the report is written locally
    # off predictions.jsonl, which needs nothing.
    evaluate.main([
        "generate",
        "--test", "/vol/test.jsonl",
        "--adapter", f"{out_dir}/adapter",
        "--out", f"{out_dir}/predictions.jsonl",
    ])
    vol.commit()
    return out_dir


@app.local_entrypoint()
def main(
    step: str = "train", run_name: str = "style-v1", limit: int = 0, wait: bool = True
) -> None:
    """--no-wait spawns the job and returns immediately.

    `.remote()` blocks the local client for the whole run, and Modal cancels the
    remote function the moment that client dies - which loses a half-finished
    fine-tune to any local hiccup. `modal run --detach` keeps the app alive but
    not the blocking call, so long runs want --no-wait instead.
    """
    if step not in {"generate", "train", "all"}:
        raise SystemExit(f"unknown --step {step!r}; use generate, train or all")

    if not wait:
        call = (generate if step == "generate" else train).spawn(
            **({"limit": limit} if step == "generate" else {"run_name": run_name})
        )
        print(f"spawned {step}: {call.object_id}")
        print(f"follow it with: modal app logs {app.app_id}")
        return

    if step in {"generate", "all"}:
        print(generate.remote(limit=limit))
    if step in {"train", "all"}:
        print(f"done -> {train.remote(run_name=run_name)}")
    print("pull results with: modal volume get style-ft /outputs ./outputs")
