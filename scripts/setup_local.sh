#!/usr/bin/env bash
# Local (no GPU) env: pipeline steps 2, 3, 5-reporting. Works on macOS.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pip install -e .
echo
echo "done. activate with:  source .venv/bin/activate"
echo "then:                 bash scripts/smoke_test.sh"
