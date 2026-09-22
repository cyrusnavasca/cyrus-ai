"""Is the chat adapter underfit, and does sampling temperature explain the noise?

Two questions the training log cannot answer once it has scrolled away:

  1. How much did the adapter actually learn? Held-out loss for the base model
     and for the adapter on the same pairs, scored on assistant tokens only -
     the same tokens training optimized. A final training loss of ~3.25 looks
     alarming in isolation, but texting is high-entropy: the honest reading is
     the gap to base, not the absolute number.
  2. How much of the "random message with no thought" feel is the sampler
     rather than the weights? Same context, several temperatures.

  modal run tools/probe_fit.py --run-name chat-v3
"""

import pathlib

import modal

REPO = pathlib.Path(__file__).resolve().parents[1]
app = modal.App("style-ft-probe")
vol = modal.Volume.from_name("style-ft")
hf_cache = modal.Volume.from_name("style-ft-hf-cache")
image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .pip_install("unsloth", "unsloth_zoo", "trl", "peft", "datasets", "bitsandbytes", "accelerate")
    .add_local_dir(REPO / "src" / "style_ft", "/root/style_ft")
)


@app.function(image=image, gpu="A100", volumes={"/vol": vol, "/root/.cache/huggingface": hf_cache}, timeout=3600)
def probe(run_name: str = "chat-v3", n: int = 300) -> dict:
    import json
    import sys

    from unsloth import FastLanguageModel  # isort: skip
    import torch

    sys.path.insert(0, "/root")
    from style_ft.modeling.formatting import IGNORE_INDEX, masked_example

    cfg = json.loads(pathlib.Path(f"/vol/outputs/{run_name}/training_config.json").read_text())
    my_name = cfg.get("my_name", "Me")
    with open("/vol/chat_test.jsonl") as fh:
        pairs = [json.loads(line) for line in fh][:n]

    def mean_nll(model, tokenizer):
        total = count = 0.0
        for p in pairs:
            ex = masked_example(p, tokenizer, 2048, my_name=my_name)
            if ex is None:
                continue
            ids = torch.tensor([ex["input_ids"]], device=model.device)
            labels = torch.tensor([ex["labels"]], device=model.device)
            with torch.no_grad():
                out = model(input_ids=ids, labels=labels)
            n_tok = int((labels != IGNORE_INDEX).sum()) - 1
            total += float(out.loss) * n_tok
            count += n_tok
        return total / max(1.0, count)

    results = {}
    base, tok = FastLanguageModel.from_pretrained(
        model_name=cfg["base_model"], max_seq_length=2048, dtype=None, load_in_4bit=True)
    FastLanguageModel.for_inference(base)
    results["base_nll"] = mean_nll(base, tok)
    del base
    torch.cuda.empty_cache()

    tuned, tok = FastLanguageModel.from_pretrained(
        model_name=f"/vol/outputs/{run_name}/adapter", max_seq_length=2048,
        dtype=None, load_in_4bit=True)
    FastLanguageModel.for_inference(tuned)
    results["tuned_nll"] = mean_nll(tuned, tok)

    # Same context, several temperatures, several samples each.
    from style_ft.modeling.formatting import messages_for

    ctx = {"context": [{"speaker": "Friend 1", "text": "have you heard from joe?"}],
           "output": "", "meta": {"with": "Friend 1", "is_group": False}}
    text = tok.apply_chat_template(
        messages_for(ctx, include_response=False, my_name=my_name),
        tokenize=False, add_generation_prompt=True)
    inp = tok(text, return_tensors="pt").to(tuned.device)
    samples = {}
    for temp in (0.3, 0.7, 1.0):
        outs = []
        for _ in range(5):
            with torch.no_grad():
                o = tuned.generate(**inp, max_new_tokens=64, do_sample=True,
                                   temperature=temp, top_p=0.9, repetition_penalty=1.1,
                                   pad_token_id=tok.eos_token_id)
            outs.append(tok.decode(o[0][inp["input_ids"].shape[1]:],
                                   skip_special_tokens=True).strip())
        samples[str(temp)] = outs
    results["samples"] = samples
    results["n_scored"] = len(pairs)
    return results


@app.local_entrypoint()
def main(run_name: str = "chat-v3", n: int = 300):
    import json

    r = probe.remote(run_name=run_name, n=n)
    b, t = r["base_nll"], r["tuned_nll"]
    print(f"\nheld-out loss on {r['n_scored']} pairs ({run_name})")
    print(f"  base  {b:.3f}   ppl {2.718281828 ** b:7.1f}")
    print(f"  tuned {t:.3f}   ppl {2.718281828 ** t:7.1f}")
    print(f"  improvement: {b - t:+.3f} nats ({100 * (b - t) / b:.1f}%)")
    print("\nsame context, by temperature:")
    print(json.dumps(r["samples"], indent=2, ensure_ascii=False))
