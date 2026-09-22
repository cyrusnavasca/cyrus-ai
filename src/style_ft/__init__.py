"""Fine-tune an open LLM on a personal iMessage history so it replies in that voice.

The package is laid out as the pipeline runs:

    ingest      raw conversations in (chat.db, or a file export)
    corpus      threads -> cleaned turns -> (context, my reply) training pairs
    modeling    prompt rendering shared by training and inference, LoRA training
    evaluation  base vs. fine-tuned generation and surface-style metrics
    common      JSONL I/O and the deterministic ranking both sampling stages use
"""

__version__ = "0.2.0"
