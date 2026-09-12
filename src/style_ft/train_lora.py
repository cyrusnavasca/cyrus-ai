"""Step 4: Unsloth LoRA fine-tune.

Requires a CUDA GPU - Unsloth does not run on Apple Silicon. Run this on Colab,
Runpod, Lambda, or any rented GPU box:

  pip install -r requirements-train.txt
  python -m style_ft.train_lora --train data/processed/train.jsonl \
      --output-dir outputs/llama3-style-v1

Smoke test the loop before real data exists (a handful of steps on the dummy
dataset, proves the whole path works end to end):

  python -m style_ft.train_lora --train data/dummy/train.jsonl \
      --output-dir outputs/smoke --max-steps 5 --base-model unsloth/Qwen2.5-0.5B-Instruct
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .formatting import to_messages
from .jsonlio import log, read_jsonl

DEFAULTS = {
    # 4-bit prequantized: fastest download, fits a 16GB card at seq 2048.
    "base_model": "unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit",
    "max_seq_length": 2048,
    "lora_rank": 16,
    # alpha == rank is the stable Unsloth recommendation; raise alpha to push
    # the style harder if the fine-tune comes out too subtle.
    "lora_alpha": 16,
    "lora_dropout": 0.0,
    "learning_rate": 2e-4,
    "epochs": 3,
    "batch_size": 2,
    "grad_accum": 4,
    "warmup_ratio": 0.05,
    "weight_decay": 0.01,
    "seed": 3407,
}

TARGET_MODULES = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", default="data/processed/train.jsonl")
    ap.add_argument("--eval", default=None, help="optional held-out file for eval loss")
    ap.add_argument("--output-dir", default="outputs/style-lora")
    ap.add_argument("--base-model", default=DEFAULTS["base_model"])
    ap.add_argument("--max-seq-length", type=int, default=DEFAULTS["max_seq_length"])
    ap.add_argument("--rank", type=int, default=DEFAULTS["lora_rank"])
    ap.add_argument("--alpha", type=int, default=DEFAULTS["lora_alpha"])
    ap.add_argument("--dropout", type=float, default=DEFAULTS["lora_dropout"])
    ap.add_argument("--lr", type=float, default=DEFAULTS["learning_rate"])
    ap.add_argument("--epochs", type=float, default=DEFAULTS["epochs"])
    ap.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    ap.add_argument("--grad-accum", type=int, default=DEFAULTS["grad_accum"])
    ap.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    ap.add_argument("--max-steps", type=int, default=None, help="overrides --epochs; use for smoke tests")
    ap.add_argument("--no-4bit", action="store_true", help="load in 16-bit instead of 4-bit")
    ap.add_argument("--chat-template", default=None, help="override Unsloth chat template name")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Imported here so --help works on a machine without a GPU stack.
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel, is_bfloat16_supported

    pairs = list(read_jsonl(args.train))
    if not pairs:
        raise SystemExit(f"No training pairs in {args.train}")
    log(f"{len(pairs)} training pairs from {args.train}")

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.base_model,
        max_seq_length=args.max_seq_length,
        dtype=None,  # auto-detect
        load_in_4bit=not args.no_4bit,
    )
    if args.chat_template:
        from unsloth.chat_templates import get_chat_template

        tokenizer = get_chat_template(tokenizer, chat_template=args.chat_template)

    model = FastLanguageModel.get_peft_model(
        model,
        r=args.rank,
        lora_alpha=args.alpha,
        lora_dropout=args.dropout,
        target_modules=TARGET_MODULES,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=args.seed,
    )

    def render(rows: list[dict]) -> Dataset:
        texts = [
            tokenizer.apply_chat_template(to_messages(p), tokenize=False)
            for p in rows
        ]
        return Dataset.from_dict({"text": texts})

    train_ds = render(pairs)
    eval_ds = render(list(read_jsonl(args.eval))) if args.eval else None
    log(f"example rendered sample:\n{train_ds[0]['text'][:600]}")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sft_config = SFTConfig(
        output_dir=str(out_dir / "checkpoints"),
        dataset_text_field="text",
        max_seq_length=args.max_seq_length,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        warmup_ratio=DEFAULTS["warmup_ratio"],
        learning_rate=args.lr,
        weight_decay=DEFAULTS["weight_decay"],
        lr_scheduler_type="linear",
        optim="adamw_8bit",
        fp16=not is_bfloat16_supported(),
        bf16=is_bfloat16_supported(),
        logging_steps=1,
        save_strategy="epoch",
        seed=args.seed,
        report_to="none",
        **({"max_steps": args.max_steps} if args.max_steps else {"num_train_epochs": args.epochs}),
        **({"eval_strategy": "epoch"} if eval_ds is not None else {}),
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        args=sft_config,
    )
    stats = trainer.train()

    adapter_dir = out_dir / "adapter"
    model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))

    (out_dir / "training_config.json").write_text(
        json.dumps(
            {
                "base_model": args.base_model,
                "train_file": args.train,
                "n_train": len(pairs),
                "lora": {"r": args.rank, "alpha": args.alpha, "dropout": args.dropout,
                          "target_modules": TARGET_MODULES},
                "lr": args.lr,
                "epochs": args.epochs,
                "max_steps": args.max_steps,
                "batch_size": args.batch_size,
                "grad_accum": args.grad_accum,
                "max_seq_length": args.max_seq_length,
                "seed": args.seed,
                "final_loss": getattr(stats, "training_loss", None),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"adapter saved -> {adapter_dir}")
    log(f"config saved  -> {out_dir / 'training_config.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
