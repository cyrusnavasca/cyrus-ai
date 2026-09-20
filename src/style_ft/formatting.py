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


# Sentinel HuggingFace uses for "do not compute loss on this token".
IGNORE_INDEX = -100


class PromptNotAPrefixError(RuntimeError):
    """The rendered prompt is not a prefix of the rendered full conversation.

    Raised rather than silently falling back to training on everything: a chat
    template that does this would make the mask boundary meaningless, and the
    resulting run would look fine while learning the wrong thing.
    """


def masked_example(
    pair: dict[str, Any], tokenizer: Any, max_seq_length: int
) -> dict[str, list[int]]:
    """Tokenize one pair, masking the prompt so loss falls on the reply alone.

    Without this the model also learns to produce the system prompt and the
    synthetic, LLM-written input - neither of which it is ever asked to generate,
    and both of which dilute the style signal we are actually training for.

    The boundary is derived from the tokenizer's own chat template rather than
    hardcoded role markers, so it stays correct across base models.
    """
    prompt = tokenizer.apply_chat_template(
        to_messages(pair, include_response=False),
        tokenize=False,
        add_generation_prompt=True,
    )
    full = tokenizer.apply_chat_template(to_messages(pair), tokenize=False)

    prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    full_ids = tokenizer(full, add_special_tokens=False)["input_ids"]
    if full_ids[: len(prompt_ids)] != prompt_ids:
        raise PromptNotAPrefixError(
            "The chat template does not render the prompt as a prefix of the full "
            "conversation, so the assistant span cannot be located."
        )

    full_ids = full_ids[:max_seq_length]
    n_prompt = min(len(prompt_ids), len(full_ids))
    labels = [IGNORE_INDEX] * n_prompt + full_ids[n_prompt:]
    return {
        "input_ids": full_ids,
        "attention_mask": [1] * len(full_ids),
        "labels": labels,
    }
