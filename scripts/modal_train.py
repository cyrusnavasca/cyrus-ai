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
    min_output_chars: int = 20,
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
    # Very short messages produced most of the first run's content corruption
    # ("I went to the park after." -> "i went to the d after"): there is barely
    # any content to anchor on, so the fine-tune invents some.
    records = [r for r in records if r.get("text") and len(r["text"]) >= min_output_chars]
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
    epochs: float = 2,
    rank: int = 16,
    max_steps: int = 0,
    train_file: str = "/vol/train.jsonl",
    test_file: str = "/vol/test.jsonl",
    my_name: str = "Me",
    grad_accum: int = 4,
) -> str:
    """Steps 4 and 5: LoRA fine-tune, then generate on the held-out test set."""
    import sys

    sys.path.insert(0, "/root")
    from style_ft import evaluate, train_lora

    out_dir = f"/vol/outputs/{run_name}"
    argv = [
        "--train", train_file,
        "--eval", test_file,
        "--output-dir", out_dir,
        "--base-model", base_model,
        "--epochs", str(epochs),
        "--rank", str(rank),
        "--my-name", my_name,
        "--grad-accum", str(grad_accum),
    ]
    if max_steps:
        argv += ["--max-steps", str(max_steps)]
    train_lora.main(argv)
    vol.commit()

    # Generation needs the GPU, so it runs here; the report is written locally
    # off predictions.jsonl, which needs nothing.
    evaluate.main([
        "generate",
        "--test", test_file,
        "--adapter", f"{out_dir}/adapter",
        "--out", f"{out_dir}/predictions.jsonl",
        "--my-name", my_name,
    ])
    vol.commit()
    return out_dir


@app.function(image=train_image, gpu="L4", volumes=VOLUMES, timeout=1800)
def ask(
    prompts: list[str],
    run_name: str = "style-v2",
    my_name: str = "Me",
    with_whom: str = "a friend",
    chat: bool = False,
) -> list[tuple[str, str, str]]:
    """Run arbitrary prompts through a trained adapter, base and tuned.

    The held-out report only shows whatever happened to be first in the test
    file. This answers "what does it do if I type X".

    L4 rather than A100: inference on an 8B 4-bit model does not need the
    bigger card, and this runs while a training job holds the A100.
    """
    import sys

    sys.path.insert(0, "/root")
    import torch
    from unsloth import FastLanguageModel  # isort: skip
    from style_ft.formatting import messages_for

    adapter = f"/vol/outputs/{run_name}/adapter"
    # A chat pair is context -> reply; a style pair is one line -> restyled.
    pairs = [
        {"context": [{"speaker": with_whom, "text": p}], "output": "",
         "meta": {"with": with_whom, "is_group": False}}
        if chat else {"input": p, "output": ""}
        for p in prompts
    ]

    def run(model_name: str, is_adapter: bool) -> list[str]:
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=model_name, max_seq_length=2048, dtype=None, load_in_4bit=True
        )
        FastLanguageModel.for_inference(model)
        outs = []
        for pair in pairs:
            text = tokenizer.apply_chat_template(
                messages_for(pair, include_response=False, my_name=my_name),
                tokenize=False, add_generation_prompt=True,
            )
            inputs = tokenizer(text, return_tensors="pt").to(model.device)
            with torch.no_grad():
                gen = model.generate(**inputs, max_new_tokens=96, do_sample=True,
                                     temperature=0.8, top_p=0.95,
                                     pad_token_id=tokenizer.eos_token_id)
            outs.append(
                tokenizer.decode(gen[0][inputs["input_ids"].shape[1]:],
                                 skip_special_tokens=True).strip()
            )
        del model
        torch.cuda.empty_cache()
        return outs

    import json as _json

    cfg = _json.loads(
        pathlib.Path(f"/vol/outputs/{run_name}/training_config.json").read_text()
    )
    base_outs = run(cfg["base_model"], False)
    tuned_outs = run(adapter, True)
    return list(zip(prompts, base_outs, tuned_outs))


# No GPU: this only blocks on two calls that each request their own. Giving it
# one would idle a second A100 for the entire run.
@app.function(image=modal.Image.debian_slim(python_version="3.12"), timeout=8 * 60 * 60)
def pipeline(run_name: str = "style-v2", limit: int = 0) -> str:
    """generate then train in one spawnable call, so a long run needs no local
    client to stay alive between the two steps."""
    generate.remote(limit=limit)
    return train.remote(run_name=run_name)


def chat_kwargs(my_name: str) -> dict[str, object]:
    """Settings for the conversational format, shared by the blocking and
    spawned paths so `--wait` cannot change what gets trained.

    One epoch and a wider effective batch: 35k pairs at the style path's
    2 epochs / grad-accum 4 is roughly five GPU-hours.
    """
    return {
        "train_file": "/vol/chat_train.jsonl",
        "test_file": "/vol/chat_test.jsonl",
        "my_name": my_name,
        "epochs": 1,
        "grad_accum": 8,
    }


@app.local_entrypoint()
def main(
    step: str = "train",
    run_name: str = "style-v1",
    limit: int = 0,
    wait: bool = True,
    my_name: str = "Me",
    prompt: str = "",
) -> None:
    """--no-wait spawns the job and returns immediately.

    `.remote()` blocks the local client for the whole run, and Modal cancels the
    remote function the moment that client dies - which loses a half-finished
    fine-tune to any local hiccup. `modal run --detach` keeps the app alive but
    not the blocking call, so long runs want --no-wait instead.
    """
    if step == "ask":
        if not prompt:
            raise SystemExit("--step ask needs --prompt")
        # Semicolons so several probes share one container load.
        prompts = [p.strip() for p in prompt.split(";") if p.strip()]
        is_chat = run_name.startswith("chat")
        for text, base, tuned in ask.remote(
            prompts, run_name=run_name, my_name=my_name, chat=is_chat
        ):
            print(f"\nPROMPT: {text}\n  BASE : {base}\n  TUNED: {tuned}")
        return

    if step not in {"generate", "train", "chat", "all"}:
        raise SystemExit(f"unknown --step {step!r}; use generate, train, chat, ask or all")

    if not wait:
        # 35k conversational pairs, so one epoch and a wider effective batch:
        # at the style path's settings this would be ~5 GPU-hours.
        spawns = {
            "generate": (generate, {"limit": limit}),
            "train": (train, {"run_name": run_name}),
            # Conversational pairs are built locally by build_chat_pairs and
            # pushed to the volume, so this step never touches the generator.
            "chat": (train, {"run_name": run_name, **chat_kwargs(my_name)}),
            "all": (pipeline, {"run_name": run_name, "limit": limit}),
        }
        fn, kwargs = spawns[step]
        call = fn.spawn(**kwargs)
        print(f"spawned {step}: {call.object_id}")
        print(f"follow it with: modal app logs {app.app_id}")
        return

    if step in {"generate", "all"}:
        print(generate.remote(limit=limit))
    if step == "chat":
        print(f"done -> {train.remote(run_name=run_name, **chat_kwargs(my_name))}")
    if step in {"train", "all"}:
        print(f"done -> {train.remote(run_name=run_name)}")
    print("pull results with: modal volume get style-ft /outputs ./outputs")
