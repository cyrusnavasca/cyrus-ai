#!/usr/bin/env bash
# Training environment. CUDA GPU required - Unsloth has no Apple Silicon build.
# Run this on Colab / Runpod / Lambda / any rented GPU box.
#
# Not needed if you train through Modal (deploy/train.py); that builds its own
# image with the same dependency set.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - <<'PY'
import sys
try:
    import torch
except ImportError:
    print("torch not installed yet - continuing, unsloth will pull a build."); sys.exit(0)
print("torch", torch.__version__)
print("cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0))
    print("vram GB:", round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1))
else:
    print("WARNING: no CUDA device. Unsloth will not run here.")
PY

pip install --upgrade pip
pip install -e ".[train]"

echo
echo "GPU smoke fine-tune (tiny model, 5 steps, proves the loop):"
echo "  style-ft train --train data/dummy/chat_train.jsonl \\"
echo "      --output-dir outputs/smoke/lora --base-model unsloth/Qwen2.5-0.5B-Instruct --max-steps 5"
