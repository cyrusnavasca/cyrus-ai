"""Run the GPU-bound half of the pipeline on Modal.

Everything up to this point runs on a laptop. Training does not: Unsloth has no
Apple Silicon build, so the fine-tune and the held-out generation that follows it
happen on a rented A100, reading and writing one Modal Volume.

Usage (from the repo root):

  pip install modal && python3 -m modal setup
  modal volume create style-ft

  # push the corpus built by scripts/build_dataset.sh
  modal volume put --force style-ft data/processed/chat_train.jsonl /chat_train.jsonl
  modal volume put --force style-ft data/processed/chat_test.jsonl  /chat_test.jsonl

  # fine-tune (~25 min on an A100 for 48k pairs at 1 epoch)
  modal run --detach deploy/train.py --step train --run-name chat-v3 --my-name Cyrus --no-wait

  # ask the adapter something without pulling it down
  modal run deploy/train.py --step ask --run-name chat-v3 --prompt "yo u up; wyd"

  # pull the results back
  modal volume get style-ft /outputs ./outputs
"""

from __future__ import annotations

import pathlib

import modal

REPO = pathlib.Path(__file__).resolve().parents[1]

app = modal.App("style-ft")

# Data and results. Survives between runs, so a crashed run does not cost the
# corpus upload that preceded it.
vol = modal.Volume.from_name("style-ft", create_if_missing=True)

# Base model weights are multi-GB; caching them makes reruns start in seconds.
hf_cache = modal.Volume.from_name("style-ft-hf-cache", create_if_missing=True)

VOLUMES = {"/vol": vol, "/root/.cache/huggingface": hf_cache}

# A -devel CUDA image rather than debian_slim: Unsloth and bitsandbytes compile
# kernels at import time, so the toolkit has to be present on the image itself.
train_image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .pip_install("unsloth", "unsloth_zoo", "trl", "peft", "datasets", "bitsandbytes", "accelerate")
    .add_local_dir(REPO / "src" / "style_ft", "/root/style_ft")
)

# A100 is the sweet spot: an H100 finishes sooner but costs more per job, and no
# run here is long enough for the difference to matter.
GPU = "A100"

# 48k conversational pairs, so one epoch and a wide effective batch. At two
# epochs and grad-accum 4 the same corpus is roughly five GPU-hours.
DEFAULTS = {"epochs": 1.0, "grad_accum": 8}


@app.function(image=train_image, gpu=GPU, volumes=VOLUMES, timeout=6 * 60 * 60)
def train(
    run_name: str = "chat-v1",
    base_model: str = "unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit",
    epochs: float = DEFAULTS["epochs"],
    rank: int = 16,
    max_steps: int = 0,
    train_file: str = "/vol/chat_train.jsonl",
    test_file: str = "/vol/chat_test.jsonl",
    my_name: str = "Me",
    grad_accum: int = DEFAULTS["grad_accum"],
) -> str:
    """LoRA fine-tune, then generate on the held-out test set."""
    import sys

    sys.path.insert(0, "/root")
    from style_ft.evaluation import runner
    from style_ft.modeling import train_lora

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

    # Generation needs the GPU, so it runs here; the report is rendered locally
    # off predictions.jsonl, which needs nothing.
    runner.main([
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
    run_name: str = "chat-v1",
    my_name: str = "Me",
    with_whom: str = "a friend",
) -> list[tuple[str, str, str]]:
    """Run arbitrary prompts through a trained adapter, base and tuned.

    The held-out report only shows whatever happened to be sampled into it. This
    answers "what does it do if I type X".

    L4 rather than A100: inference on an 8B 4-bit model does not need the bigger
    card, and this often runs while a training job holds the A100.
    """
    import json
    import sys

    sys.path.insert(0, "/root")
    import torch
    from unsloth import FastLanguageModel  # isort: skip
    from style_ft.modeling.formatting import messages_for

    adapter = f"/vol/outputs/{run_name}/adapter"
    pairs = [
        {"context": [{"speaker": with_whom, "text": p}], "output": "",
         "meta": {"with": with_whom, "is_group": False}}
        for p in prompts
    ]

    def run(model_name: str) -> list[str]:
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

    cfg = json.loads(
        pathlib.Path(f"/vol/outputs/{run_name}/training_config.json").read_text()
    )
    return list(zip(prompts, run(cfg["base_model"]), run(adapter), strict=True))


@app.local_entrypoint()
def main(
    step: str = "train",
    run_name: str = "chat-v1",
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
        for text, base, tuned in ask.remote(prompts, run_name=run_name, my_name=my_name):
            print(f"\nPROMPT: {text}\n  BASE : {base}\n  TUNED: {tuned}")
        return

    if step != "train":
        raise SystemExit(f"unknown --step {step!r}; use train or ask")

    kwargs = {"run_name": run_name, "my_name": my_name}
    if not wait:
        call = train.spawn(**kwargs)
        print(f"spawned train: {call.object_id}")
        print(f"follow it with: modal app logs {app.app_id}")
        return

    print(f"done -> {train.remote(**kwargs)}")
    print("pull results with: modal volume get style-ft /outputs ./outputs")
