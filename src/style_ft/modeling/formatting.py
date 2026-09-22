"""The single source of the chat messages the model sees.

Training and inference must render byte-identical prompts. They are built here,
in one place, and nowhere else: a mismatch between the two does not raise, it
just quietly degrades the fine-tune, which is the worst kind of bug to own.

A pair is `{"context": [{"speaker", "text"}, ...], "output": <my reply>}`. The
system prompt carries the persona, because "what would I say next" depends on
both who is speaking and who they are speaking to - the same adapter texts
different people differently.
"""

from __future__ import annotations

from typing import Any

CHAT_SYSTEM = (
    "You are {me}. You are texting {who}. Reply exactly as {me} would - same "
    "voice, same length, same punctuation habits. Write only the message."
)
CHAT_GROUP_SYSTEM = (
    "You are {me}. You are texting in the group chat \"{who}\". Reply exactly "
    "as {me} would - same voice, same length, same punctuation habits. Write "
    "only the message."
)


def chat_system(pair: dict[str, Any], my_name: str = "Me") -> str:
    meta = pair.get("meta", {})
    template = CHAT_GROUP_SYSTEM if meta.get("is_group") else CHAT_SYSTEM
    return template.format(me=my_name, who=meta.get("with", "someone"))


def chat_transcript(context: list[dict[str, str]]) -> str:
    return "\n".join(f"{turn['speaker']}: {turn['text']}" for turn in context)


def messages_for(
    pair: dict[str, Any], include_response: bool = True, my_name: str = "Me"
) -> list[dict[str, str]]:
    """Render one pair as chat messages: transcript in, my next message out."""
    msgs = [
        {"role": "system", "content": chat_system(pair, my_name)},
        {"role": "user", "content": chat_transcript(pair["context"])},
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
    pair: dict[str, Any], tokenizer: Any, max_seq_length: int, my_name: str = "Me"
) -> dict[str, list[int]] | None:
    """Tokenize one pair, masking the prompt so loss falls on the reply alone.

    Returns None for an example that does not fit in max_seq_length, or whose
    reply is empty after templating.

    Without this the model also learns to produce the system prompt and the
    conversation that preceded the reply - neither of which it is ever asked to
    generate, and both of which dilute the style signal being trained for.

    The boundary is derived from the tokenizer's own chat template rather than
    hardcoded role markers, so it stays correct across base models.
    """
    prompt = tokenizer.apply_chat_template(
        messages_for(pair, include_response=False, my_name=my_name),
        tokenize=False,
        add_generation_prompt=True,
    )
    full = tokenizer.apply_chat_template(
        messages_for(pair, my_name=my_name), tokenize=False
    )

    prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    full_ids = tokenizer(full, add_special_tokens=False)["input_ids"]
    if full_ids[: len(prompt_ids)] != prompt_ids:
        raise PromptNotAPrefixError(
            "The chat template does not render the prompt as a prefix of the full "
            "conversation, so the assistant span cannot be located."
        )

    # Truncating here would cut the reply, not the prompt: it can leave an
    # example with every label masked (a NaN loss for a batch of them) and it
    # strips the trailing EOS, teaching the model never to stop. Long examples
    # are dropped by the caller instead.
    if len(full_ids) > max_seq_length or len(prompt_ids) >= len(full_ids):
        return None

    n_prompt = len(prompt_ids)
    return {
        "input_ids": full_ids,
        "attention_mask": [1] * len(full_ids),
        "labels": [IGNORE_INDEX] * n_prompt + full_ids[n_prompt:],
    }
