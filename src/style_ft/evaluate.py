"""Step 5: eval harness. Base model vs fine-tuned, side by side.

Two subcommands so the reporting half runs anywhere (no GPU, no model):

  generate  load the base model and the LoRA adapter, run both over the held-out
            test inputs, write predictions.jsonl   [needs the training GPU box]
  report    turn predictions.jsonl into a markdown + CSV side-by-side report and
            a style-metric comparison                        [runs anywhere]

  python -m style_ft.evaluate generate --test data/processed/test.jsonl \
      --adapter outputs/style-lora/adapter --out outputs/style-lora/predictions.jsonl
  python -m style_ft.evaluate report --predictions outputs/style-lora/predictions.jsonl \
      --outdir outputs/style-lora
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from .formatting import messages_for
from .jsonlio import log, read_jsonl, write_jsonl
from .style_metrics import compare

GEN_DEFAULTS = {"max_new_tokens": 256, "temperature": 0.8, "top_p": 0.95, "min_p": 0.05}


# --------------------------------------------------------------------------- #
# generate
# --------------------------------------------------------------------------- #

def _generate_all(model, tokenizer, pairs: list[dict[str, Any]], args) -> list[str]:
    import torch

    outs: list[str] = []
    for i, pair in enumerate(pairs, 1):
        prompt = tokenizer.apply_chat_template(
            messages_for(pair, include_response=False),
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        with torch.no_grad():
            generated = model.generate(
                **inputs,
                max_new_tokens=args.max_new_tokens,
                temperature=args.temperature,
                top_p=args.top_p,
                min_p=args.min_p,
                do_sample=True,
                pad_token_id=tokenizer.eos_token_id,
            )
        text = tokenizer.decode(
            generated[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
        ).strip()
        outs.append(text)
        if i % 10 == 0:
            log(f"  {i}/{len(pairs)}")
    return outs


def cmd_generate(args) -> int:
    from unsloth import FastLanguageModel

    pairs = list(read_jsonl(args.test))
    if args.limit:
        pairs = pairs[: args.limit]
    if not pairs:
        raise SystemExit(f"No test pairs in {args.test}")
    log(f"{len(pairs)} test pairs")

    cfg_path = Path(args.adapter).parent / "training_config.json"
    base_model = args.base_model
    if base_model is None and cfg_path.exists():
        base_model = json.loads(cfg_path.read_text(encoding="utf-8"))["base_model"]
        log(f"base model from {cfg_path}: {base_model}")
    if base_model is None:
        raise SystemExit("--base-model is required when training_config.json is absent")

    log("loading base model...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=base_model,
        max_seq_length=args.max_seq_length,
        dtype=None,
        load_in_4bit=not args.no_4bit,
    )
    FastLanguageModel.for_inference(model)
    base_outputs = _generate_all(model, tokenizer, pairs, args)

    log("loading fine-tuned adapter...")
    del model
    import gc

    import torch

    gc.collect()
    torch.cuda.empty_cache()
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.adapter,
        max_seq_length=args.max_seq_length,
        dtype=None,
        load_in_4bit=not args.no_4bit,
    )
    FastLanguageModel.for_inference(model)
    tuned_outputs = _generate_all(model, tokenizer, pairs, args)

    rows = [
        {
            "id": p.get("id", ""),
            "input": p["input"],
            "reference": p["output"],
            "base_output": b,
            "tuned_output": t,
        }
        for p, b, t in zip(pairs, base_outputs, tuned_outputs)
    ]
    write_jsonl(args.out, rows)
    log(f"predictions -> {args.out}")
    return 0


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def cmd_report(args) -> int:
    rows = list(read_jsonl(args.predictions))
    if not rows:
        raise SystemExit(f"No predictions in {args.predictions}")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    metrics = compare(
        [r["reference"] for r in rows],
        [r.get("base_output", "") for r in rows],
        [r.get("tuned_output", "") for r in rows],
    )
    tuned_wins = sum(1 for v in metrics.values() if v["closer"] == "tuned")

    md = [
        "# Style transfer eval",
        "",
        f"Source: `{args.predictions}` — {len(rows)} held-out samples",
        "",
        "## Style metrics",
        "",
        f"Fine-tune is closer to the reference on **{tuned_wins}/{len(metrics)}** surface features.",
        "",
        "| feature | reference | base | tuned | closer |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for key, v in metrics.items():
        md.append(f"| {key} | {v['reference']} | {v['base']} | {v['tuned']} | {v['closer']} |")

    md += ["", "## Side by side", ""]
    for i, r in enumerate(rows, 1):
        md += [
            f"### {i}. `{r.get('id', '')}`",
            "",
            f"**Input**<br>{_md_cell(r['input'])}",
            "",
            "| | text |",
            "| --- | --- |",
            f"| reference (me) | {_md_cell(r['reference'])} |",
            f"| base | {_md_cell(r.get('base_output', ''))} |",
            f"| **fine-tuned** | {_md_cell(r.get('tuned_output', ''))} |",
            "",
        ]

    md_path = outdir / "eval_report.md"
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    csv_path = outdir / "eval_report.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "input", "reference", "base_output", "tuned_output"])
        for r in rows:
            writer.writerow([
                r.get("id", ""), r["input"], r["reference"],
                r.get("base_output", ""), r.get("tuned_output", ""),
            ])

    metrics_path = outdir / "eval_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    log(f"report  -> {md_path}")
    log(f"csv     -> {csv_path}")
    log(f"metrics -> {metrics_path}")
    log(f"fine-tune closer on {tuned_wins}/{len(metrics)} features")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="run base + fine-tuned over the test set (needs GPU)")
    g.add_argument("--test", default="data/processed/test.jsonl")
    g.add_argument("--adapter", default="outputs/style-lora/adapter")
    g.add_argument("--base-model", default=None, help="defaults to the value in training_config.json")
    g.add_argument("--out", default="outputs/style-lora/predictions.jsonl")
    g.add_argument("--limit", type=int, default=25)
    g.add_argument("--max-seq-length", type=int, default=2048)
    g.add_argument("--max-new-tokens", type=int, default=GEN_DEFAULTS["max_new_tokens"])
    g.add_argument("--temperature", type=float, default=GEN_DEFAULTS["temperature"])
    g.add_argument("--top-p", type=float, default=GEN_DEFAULTS["top_p"])
    g.add_argument("--min-p", type=float, default=GEN_DEFAULTS["min_p"])
    g.add_argument("--no-4bit", action="store_true")
    g.set_defaults(func=cmd_generate)

    r = sub.add_parser("report", help="render the side-by-side report (no GPU)")
    r.add_argument("--predictions", default="outputs/style-lora/predictions.jsonl")
    r.add_argument("--outdir", default="outputs/style-lora")
    r.set_defaults(func=cmd_report)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
