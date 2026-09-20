"""Throwaway diagnostic: what does the installed TRL/Unsloth stack actually expose?

Three runs guessing at TRL's surface cost more than one run asking it.

  modal run scripts/modal_probe_trl.py
"""

from __future__ import annotations

import pathlib

import modal

REPO = pathlib.Path(__file__).resolve().parents[1]
app = modal.App("style-ft-probe")

image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .pip_install("unsloth", "unsloth_zoo", "trl", "peft", "datasets", "bitsandbytes", "accelerate")
)
hf_cache = modal.Volume.from_name("style-ft-hf-cache", create_if_missing=True)


@app.function(image=image, gpu="A100", volumes={"/root/.cache/huggingface": hf_cache}, timeout=1800)
def probe() -> None:
    import dataclasses
    import inspect

    import trl
    from unsloth import FastLanguageModel  # noqa: F401  (import order matters to unsloth)
    from trl import SFTConfig, SFTTrainer

    print("trl version:", trl.__version__)

    fields = {f.name: f for f in dataclasses.fields(SFTConfig)}
    print("is dataclass:", dataclasses.is_dataclass(SFTConfig))
    print("eos_token in fields:", "eos_token" in fields)
    if "eos_token" in fields:
        print("  declared default:", repr(fields["eos_token"].default))
    for name in ("max_length", "max_seq_length", "pad_token", "assistant_only_loss"):
        print(f"{name} in fields:", name in fields)

    cfg = SFTConfig(output_dir="/tmp/x")
    print("instantiated .eos_token:", repr(getattr(cfg, "eos_token", "<absent>")))
    print("init signature params:", list(inspect.signature(SFTConfig.__init__).parameters)[:6])
    print("trainer signature params:", list(inspect.signature(SFTTrainer.__init__).parameters)[:8])

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name="unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit",
        max_seq_length=2048,
        dtype=None,
        load_in_4bit=True,
    )
    print("tokenizer.eos_token:", repr(tokenizer.eos_token))
    print("tokenizer.pad_token:", repr(tokenizer.pad_token))


@app.local_entrypoint()
def main() -> None:
    probe.remote()
