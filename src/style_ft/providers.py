"""Pair-generation backends.

dummy      - offline, deterministic. Lets the scaffold be tested end to end with
             no API key and no cost. Naive de-slang + sentence-case.
anthropic  - Claude via the Messages API (sync, threaded) or the Batches API
             (50% cheaper, async, best for thousands of messages).
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Iterable

from .jsonlio import log
from .prompts import SYSTEM_PROMPT, build_user_prompt

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 2048
# Style rewriting is a simple, high-volume task - low effort keeps cost sane.
DEFAULT_EFFORT = "low"

# --------------------------------------------------------------------------- #
# dummy provider
# --------------------------------------------------------------------------- #

_EXPANSIONS = {
    "u": "you", "ur": "your", "r": "are", "y": "why", "k": "okay", "ok": "okay",
    "pls": "please", "plz": "please", "thx": "thanks", "ty": "thank you",
    "idk": "I do not know", "imo": "in my opinion", "btw": "by the way",
    "rn": "right now", "tmrw": "tomorrow", "tmr": "tomorrow", "tn": "tonight",
    "omw": "on my way", "brb": "I will be right back", "gonna": "going to",
    "wanna": "want to", "kinda": "somewhat", "sry": "sorry", "w/": "with",
    "b4": "before", "cuz": "because", "bc": "because", "prob": "probably",
    "def": "definitely", "lmk": "let me know", "nvm": "never mind",
}
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⬀-⯿]+"
)


def dummy_neutralize(message: str, input_style: str = "neutral") -> str:
    """Crude style-stripper. Good enough to prove the plumbing, not the data."""
    text = _EMOJI_RE.sub("", message)
    text = re.sub(r"([!?.])\1{1,}", r"\1", text)  # "what???" -> "what?"
    words = [
        _EXPANSIONS.get(re.sub(r"[^\w/]", "", w).lower(), w) for w in text.split()
    ]
    text = re.sub(r"\s+", " ", " ".join(words)).strip()
    if not text:
        return "Acknowledge the previous message."
    if input_style == "bullet":
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text) if p.strip()]
        return "\n".join(f"- {p[0].upper() + p[1:]}" for p in parts[:4])
    if input_style == "topic":
        return f"Respond about: {text[:80].rstrip().rstrip('.')}."
    return text[0].upper() + text[1:] + ("" if text[-1] in ".!?" else ".")


# --------------------------------------------------------------------------- #
# anthropic provider
# --------------------------------------------------------------------------- #


def _require_anthropic() -> Any:
    try:
        import anthropic  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "The anthropic SDK is not installed. `pip install -r requirements.txt`, "
            "or run with --provider dummy."
        ) from exc
    return anthropic


def _text_of(message: Any) -> str:
    return "".join(b.text for b in message.content if b.type == "text").strip()


def make_request_params(message: str, input_style: str, model: str, max_tokens: int) -> dict[str, Any]:
    """The Messages API body. Shared by the sync and batch paths."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "output_config": {"effort": DEFAULT_EFFORT},
        "messages": [{"role": "user", "content": build_user_prompt(message, input_style)}],
    }


def anthropic_generate_one(
    client: Any,
    message: str,
    input_style: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_retries: int = 5,
) -> str | None:
    """Returns the generated input, or None if the model declined / kept failing."""
    anthropic = _require_anthropic()
    params = make_request_params(message, input_style, model, max_tokens)
    for attempt in range(max_retries):
        try:
            resp = client.messages.create(**params)
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError):
            if attempt == max_retries - 1:
                raise
            time.sleep(min(2**attempt, 30))
            continue
        if resp.stop_reason == "refusal":
            log(f"refusal (category={getattr(resp.stop_details, 'category', None)}); skipping sample")
            return None
        return _text_of(resp) or None
    return None


def anthropic_generate_batch(
    client: Any,
    items: Iterable[tuple[str, str]],
    input_style: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    poll_seconds: int = 60,
    on_batch_created: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Submit (custom_id, message) pairs through the Batches API. 50% cheaper.

    Blocks until the batch ends (usually < 1h, max 24h). Returns {custom_id: input}.
    """
    _require_anthropic()
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests = [
        Request(
            custom_id=cid,
            params=MessageCreateParamsNonStreaming(
                **make_request_params(msg, input_style, model, max_tokens)
            ),
        )
        for cid, msg in items
    ]
    if not requests:
        return {}
    batch = client.messages.batches.create(requests=requests)
    log(f"batch {batch.id} submitted with {len(requests)} requests")
    if on_batch_created:
        on_batch_created(batch.id)
    return anthropic_collect_batch(client, batch.id, poll_seconds)


def anthropic_collect_batch(client: Any, batch_id: str, poll_seconds: int = 60) -> dict[str, str]:
    """Poll an existing batch to completion and collect its results."""
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        counts = batch.request_counts
        log(f"batch {batch_id}: {batch.processing_status} "
            f"(processing={counts.processing} succeeded={counts.succeeded} errored={counts.errored})")
        time.sleep(poll_seconds)

    out: dict[str, str] = {}
    refused = errored = 0
    # Results come back in arbitrary order - always key by custom_id.
    for result in client.messages.batches.results(batch_id):
        kind = result.result.type
        if kind == "succeeded":
            msg = result.result.message
            if msg.stop_reason == "refusal":
                refused += 1
                continue
            text = _text_of(msg)
            if text:
                out[result.custom_id] = text
        else:
            errored += 1
    log(f"batch {batch_id} done: {len(out)} usable, {refused} refused, {errored} errored/canceled/expired")
    return out
