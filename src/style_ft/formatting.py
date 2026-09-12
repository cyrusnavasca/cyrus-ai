"""Shared conversion from a pair record to chat messages.

Kept in its own module so training and evaluation can never drift apart - the
prompt the model is trained on must be byte-identical to the one used at
inference, or the style transfer degrades for no visible reason.
"""

from __future__ import annotations

from typing import Any

from .prompts import TRAINING_INSTRUCTION

SYSTEM = "You write text messages in the user's personal style."


def user_turn(generated_input: str) -> str:
    return f"{TRAINING_INSTRUCTION}\n\n{generated_input.strip()}"


def to_messages(pair: dict[str, Any], include_response: bool = True) -> list[dict[str, str]]:
    msgs = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_turn(pair["input"])},
    ]
    if include_response:
        msgs.append({"role": "assistant", "content": pair["output"]})
    return msgs
