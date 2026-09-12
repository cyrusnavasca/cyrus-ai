"""Prompt templates for the pair-generation (reverse-engineering) step.

The fine-tune learns `input -> output`, where `output` is a real message I wrote
and `input` is a synthetic, style-free description of what that message says.
We generate the input by asking Claude to strip the style out of the real
message. Three input styles are supported:

  neutral  A flat, corporate-neutral rewrite of the same content.
           Best default: the model learns "restyle this text as me".
  bullet   A terse bullet list of the points the message makes.
           Use when the end-use is "expand my notes into a message".
  topic    A one-line description of the topic/intent only.
           Loosest coupling; risks the model inventing content at inference.

Design rules baked into the prompts:
  1. The generated input must NOT contain the style markers we want to learn
     (slang, lowercasing, abbreviations, emoji, punctuation quirks) - otherwise
     the model can copy them from the input instead of learning them.
  2. It must preserve the content, so that input->output stays a faithful pair.
  3. Output is plain text only - no preamble, no quotes, no JSON - so parsing
     is trivial and can't half-fail.
"""

from __future__ import annotations

SYSTEM_PROMPT = """You build training data for a writing-style transfer model.

You are given a real text message written by one person. Produce the INPUT that \
a style-transfer model should have been given to produce that message as OUTPUT.

Hard rules:
- Preserve all factual content, names, numbers, questions and requests.
- Strip every trace of personal style: slang, abbreviations, emoji, lowercasing, \
run-on punctuation, inside jokes phrasing, filler words. Write in plain, neutral, \
grammatically standard English.
- Never copy a distinctive phrase verbatim from the message.
- Do not add information that is not in the message.
- Do not address the model, explain yourself, or use quotation marks.
- Reply with the input text and nothing else."""

_STYLE_INSTRUCTIONS = {
    "neutral": (
        "Rewrite the message as a neutral, plainly-worded version of the same "
        "message. Same content, same length ballpark, zero personality."
    ),
    "bullet": (
        "Summarize the message as 1-4 terse bullet points (one per line, each "
        "starting with '- ') covering every point it makes. No personality."
    ),
    "topic": (
        "Describe, in one neutral sentence, what this message is about and what "
        "it is trying to accomplish. No personality, no quoted phrases."
    ),
}

USER_TEMPLATE = """{instruction}

Message:
<message>
{message}
</message>"""

# Prepended to the generated input at training/inference time, so the model sees
# a consistent task framing. Kept separate from the generation prompt on purpose.
TRAINING_INSTRUCTION = (
    "Rewrite the following in my personal texting style. Keep the meaning intact."
)


def input_styles() -> list[str]:
    return list(_STYLE_INSTRUCTIONS)


def build_user_prompt(message: str, input_style: str = "neutral") -> str:
    if input_style not in _STYLE_INSTRUCTIONS:
        raise ValueError(f"unknown input_style {input_style!r}; choose from {input_styles()}")
    return USER_TEMPLATE.format(
        instruction=_STYLE_INSTRUCTIONS[input_style], message=message.strip()
    )
