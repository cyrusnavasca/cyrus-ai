#!/usr/bin/env bash
# Local environment: everything except training. Works on macOS, no GPU.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -e ".[dev]"
echo
echo "done. activate with:  source .venv/bin/activate"
echo "then:                 bash scripts/smoke_test.sh"
