"""Does SFTTrainer actually honour a pre-supplied `labels` column?

train_lora builds masked labels by hand. If TRL's default collator rebuilds
labels from input_ids, that mask is silently discarded and the fine-tune trains
on the prompt after all - while the log line still claims otherwise.

CPU only; no model is loaded.

  modal run scripts/modal_probe_collator.py
"""

from __future__ import annotations

import modal

app = modal.App("style-ft-probe-collator")

image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "trl==0.23.0", "transformers", "datasets", "torch",
    extra_options="--extra-index-url https://download.pytorch.org/whl/cpu",
)


@app.function(image=image, timeout=900)
def probe() -> None:
    from datasets import Dataset
    from transformers import AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    tok = AutoTokenizer.from_pretrained("hf-internal-testing/llama-tokenizer")
    tok.pad_token = tok.eos_token

    # Two rows shaped exactly like formatting.masked_example returns.
    rows = [
        {"input_ids": [1, 2, 3, 4, 5], "attention_mask": [1] * 5, "labels": [-100, -100, -100, 4, 5]},
        {"input_ids": [1, 2, 3, 4, 5, 6], "attention_mask": [1] * 6, "labels": [-100, -100, 3, 4, 5, 6]},
    ]
    ds = Dataset.from_list(rows)
    print("dataset columns:", ds.column_names)

    cfg = SFTConfig(output_dir="/tmp/probe", report_to="none", max_steps=1,
                    use_cpu=True, bf16=False, fp16=False)
    trainer = SFTTrainer(model="hf-internal-testing/tiny-random-LlamaForCausalLM",
                         train_dataset=ds, args=cfg, processing_class=tok)

    print("collator class:", type(trainer.data_collator).__name__)
    print("train_dataset columns after prep:", trainer.train_dataset.column_names)

    batch = trainer.data_collator([dict(r) for r in rows])
    print("batch keys:", sorted(batch.keys()))
    labels = batch["labels"].tolist()
    print("collated labels:", labels)

    expected_masked = [3, 2]  # leading -100 counts per row
    actual_masked = [sum(1 for t in row if t == -100) for row in labels]
    print("masked-token counts: expected>=", expected_masked, "actual", actual_masked)
    print(
        "VERDICT:",
        "labels PRESERVED" if all(a >= e for a, e in zip(actual_masked, expected_masked))
        else "labels DISCARDED - hand-built mask is a no-op",
    )


@app.local_entrypoint()
def main() -> None:
    probe.remote()
