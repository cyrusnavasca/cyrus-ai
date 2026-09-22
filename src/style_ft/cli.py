"""One entrypoint for every pipeline stage: `style-ft <stage> [options]`.

The stages are deliberately separate processes with JSONL files between them.
Each one is slow, each one is worth inspecting before the next, and a pipeline
you can stop halfway is the one you can actually debug.

Each stage is also runnable on its own (`python -m style_ft.corpus.chat_pairs`);
this module only saves the caller from typing the package path.
"""

from __future__ import annotations

import argparse
import importlib
import sys

# stage -> (module, one-line help). Modules are imported lazily: `train` pulls
# in torch and unsloth, which must not be a prerequisite for `style-ft --help`
# on a laptop with no GPU.
STAGES: dict[str, tuple[str, str]] = {
    "imessage": ("style_ft.ingest.imessage_db", "read the macOS chat.db (needs Full Disk Access)"),
    "export": ("style_ft.ingest.parse_export", "read a CSV / XML / JSON message export"),
    "dummy-export": ("style_ft.ingest.dummy_export", "write synthetic export files for testing"),
    "threads": ("style_ft.corpus.select_threads", "list, include and exclude conversations"),
    "clean": ("style_ft.corpus.clean_messages", "strip corpus artifacts and apply edits"),
    "pairs": ("style_ft.corpus.chat_pairs", "build (context -> my reply) training pairs"),
    "split": ("style_ft.corpus.split_dataset", "hash-based train/test split"),
    "train": ("style_ft.modeling.train_lora", "LoRA fine-tune (needs a CUDA GPU)"),
    "eval": ("style_ft.evaluation.runner", "generate held-out predictions, render the report"),
}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="style-ft",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="stages:\n" + "\n".join(f"  {k:<13} {h}" for k, (_, h) in STAGES.items()),
    )
    ap.add_argument("stage", choices=list(STAGES), metavar="stage")
    ap.add_argument("args", nargs=argparse.REMAINDER, help="passed through to the stage")
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in {"-h", "--help"}:
        build_parser().print_help()
        return 0
    args = build_parser().parse_args(argv)
    module = importlib.import_module(STAGES[args.stage][0])
    return module.main(args.args)


if __name__ == "__main__":
    sys.exit(main())
