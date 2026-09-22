#!/usr/bin/env bash
# Rebuild the conversational dataset from the curated thread set.
#
# Configuration lives in .env, which is gitignored - it names real people. See
# .env.example for the format and for the reasoning behind curating threads by
# hand rather than by a cutoff.
#
#   bash scripts/build_dataset.sh
#
# Then push and train:
#   modal volume put --force style-ft data/processed/chat_train.jsonl /chat_train.jsonl
#   modal volume put --force style-ft data/processed/chat_test.jsonl  /chat_test.jsonl
#   modal run --detach deploy/train.py --step train --run-name chat-v3 \
#       --my-name Cyrus --no-wait

set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src

ENV_FILE=${ENV_FILE:-.env}
[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE - copy .env.example and fill it in"; exit 1; }
set -a; . "./$ENV_FILE"; set +a

: "${THREADS_INCLUDE:?set THREADS_INCLUDE in $ENV_FILE}"

# Comma-separated lists become repeated --include / --exclude flags. Split on
# commas only, so a group chat name keeps its spaces.
args=()
split_into() {
    local flag=$1 list=$2 item
    while IFS= read -r item; do
        item="$(echo "$item" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        [ -n "$item" ] && args+=("$flag" "$item")
    done <<< "${list//,/$'\n'}"
}
split_into --include "$THREADS_INCLUDE"
split_into --exclude "${THREADS_EXCLUDE:-}"

python3 -m style_ft.cli threads \
    --input data/interim/messages.jsonl \
    --output data/interim/selected.jsonl \
    "${args[@]}"

# Strip artifacts that survive the reader: attachment-only rows, chat.db's
# "Edited to ..." edit records (applied to the message they correct rather than
# dropped), pasted lyrics and homework, consecutive duplicates, bare links.
python3 -m style_ft.cli clean \
    --input data/interim/selected.jsonl \
    --output data/interim/clean.jsonl

# No --max-per-thread: capping is deliberately off. The largest thread is 58% of
# the result, which is the intent - the model should sound like this person with
# *these* people, not like an average of everyone.
#
# (select_threads' own --max-per-thread must never be used for this dataset: it
# drops a random subset of *your* messages from inside threads, which punches
# holes in the conversations the context windows are built from.)
python3 -m style_ft.cli pairs \
    --input data/interim/clean.jsonl \
    --output data/processed/chat_pairs.jsonl \
    --my-name "${MY_NAME:?set MY_NAME in $ENV_FILE}"

python3 -m style_ft.cli split \
    --input data/processed/chat_pairs.jsonl \
    --train data/processed/chat_train.jsonl \
    --test data/processed/chat_test.jsonl
