"""Pair-generation backends.

dummy      - offline, deterministic. Lets the scaffold be tested end to end with
             no API key and no cost. Naive de-slang + sentence-case.
anthropic  - Claude via the Messages API (sync, threaded) or the Batches API
             (50% cheaper, async, best for thousands of messages).
ollama     - a local open-weights model over the Ollama HTTP API. Free, and no
             message text leaves the machine. Small models follow the "reply
             with the text and nothing else" rule unreliably, so every
             generation goes through `clean_generated` before it is accepted.
"""

from __future__ import annotations

import difflib
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable

from .jsonlio import log
from .prompts import SYSTEM_PROMPT, build_user_prompt

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 2048
# Style rewriting is a simple, high-volume task - low effort keeps cost sane.
DEFAULT_EFFORT = "low"

# Ollama defaults. llama3.2 (3B) is the largest that runs comfortably on an 8GB
# Mac; on a GPU box prefer qwen2.5:7b-instruct or llama3.1:8b-instruct, which
# make far fewer of the mistakes `clean_generated` has to catch.
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"

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


# --------------------------------------------------------------------------- #
# generation hygiene (shared, but only really needed by small local models)
# --------------------------------------------------------------------------- #

# A small model asked for "the text and nothing else" answers with a preamble
# maybe one time in twenty. Cheap to strip, expensive to leave in: the preamble
# becomes part of the training input and the fine-tune learns to expect it.
_PREAMBLE_RE = re.compile(
    r"^\s*(?:sure[,!.]?\s*)?(?:here(?:'s| is| are)|the following is|neutral version|"
    r"rewritten|rewrite|plain version|output|input)\b[^\n:]{0,60}:\s*",
    re.IGNORECASE,
)
_SLANG_LEAK = set(_EXPANSIONS) - {"ok", "def"}  # 'ok'/'def' appear in normal prose

# Above this the "neutral" input is really just the message again, so the pair
# teaches an identity mapping instead of a style. Measured against a real 7B
# run: degenerate pairs scored 0.92+, while genuine restyles ("ur ... gosh 😩"
# <- "You are ...") topped out at 0.88.
#
# Deliberately compared on near-raw text. Normalizing case and punctuation away
# first looks tidier but destroys the very markers that distinguish a good pair
# from a copy, and rejects a third of a healthy dataset.
_MAX_SIMILARITY = 0.90


def _collapsed(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _comparable(text: str) -> str:
    """Lowercased, depunctuated form - for asking 'is this the same sentence?'"""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _unshout(text: str) -> str:
    """Sentence-case a shouted line.

    Only reached when _is_shouting() is true - a line that is overwhelmingly
    uppercase - so flattening the whole thing is right. A normal sentence that
    merely contains an initialism ("AP Literature") never gets here.
    """
    out = text.lower().strip()
    return out[0].upper() + out[1:] if out else out


def _is_shouting(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:  # "OK" or an initialism is not shouting
        return False
    return sum(c.isupper() for c in letters) / len(letters) > 0.7


def clean_generated(text: str, original: str, input_style: str = "neutral") -> str | None:
    """Normalize a raw generation, or return None if it is not usable as an input.

    Rejections are the point: a bad pair is worse than a missing one, because it
    teaches the model to map noise onto a real message.
    """
    if not text:
        return None
    out = text.strip()
    out = _PREAMBLE_RE.sub("", out).strip()
    # Whole-output wrapping quotes, which the prompt forbids and small models add.
    if len(out) >= 2 and out[0] in "\"'“‘" and out[-1] in "\"'”’":
        out = out[1:-1].strip()
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    if not out:
        return None

    # An echo means no style was stripped - the model would learn to copy rather
    # than restyle. Compared on a depunctuated form, because "omw!" -> "omw" is
    # just as useless a pair as an exact repeat.
    if not _comparable(out) or _comparable(out) == _comparable(original):
        return None
    similarity = difflib.SequenceMatcher(
        None, _collapsed(out), _collapsed(original)
    ).ratio()
    if similarity > _MAX_SIMILARITY:
        return None
    # Shouting is a style marker the fine-tune should learn, so an input that
    # shouts has handed the model the answer. Rejecting the pair also throws
    # away the message - and with it every all-caps example in the corpus, which
    # is how a first attempt at this lost the user's ALL-CAPS register entirely.
    # Fix the input instead and keep the pair.
    if _is_shouting(out):
        out = _unshout(out)
    # Runaway generation: the input should be the same ballpark as the message.
    if len(out) > max(400, 4 * len(original)):
        return None
    if input_style == "bullet" and not out.lstrip().startswith("-"):
        return None
    # Style markers the input is explicitly supposed to be free of.
    if _EMOJI_RE.search(out):
        return None
    words = {re.sub(r"[^\w]", "", w).lower() for w in out.split()}
    if words & _SLANG_LEAK:
        return None
    return out


# --------------------------------------------------------------------------- #
# ollama provider
# --------------------------------------------------------------------------- #


def _ollama_post(url: str, path: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ollama_check(url: str = DEFAULT_OLLAMA_URL, model: str = DEFAULT_OLLAMA_MODEL) -> None:
    """Fail loudly at startup rather than 5000 times in a row."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=10) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(
            f"Cannot reach Ollama at {url} ({exc}). Start it with `ollama serve`."
        ) from exc
    names = {m.get("name", "") for m in tags.get("models", [])}
    if model not in names and f"{model}:latest" not in names:
        raise SystemExit(
            f"Model {model!r} is not pulled. Available: {sorted(names) or 'none'}. "
            f"Run `ollama pull {model}`."
        )


def ollama_generate_one(
    message: str,
    input_style: str,
    model: str = DEFAULT_OLLAMA_MODEL,
    url: str = DEFAULT_OLLAMA_URL,
    max_tokens: int = 512,
    timeout: int = 300,
    max_retries: int = 3,
) -> str | None:
    """Returns a cleaned generated input, or None if unusable after retries."""
    body = {
        "model": model,
        "stream": False,
        # Low temperature: this is a transcription-like task, not a creative one.
        "options": {"temperature": 0.2, "num_predict": max_tokens},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(message, input_style)},
        ],
    }
    for attempt in range(max_retries):
        try:
            resp = _ollama_post(url, "/api/chat", body, timeout)
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            if attempt == max_retries - 1:
                return None
            time.sleep(min(2**attempt, 15))
            continue
        cleaned = clean_generated(
            resp.get("message", {}).get("content", ""), message, input_style
        )
        if cleaned:
            return cleaned
        # A rejected generation is usually a one-off formatting slip; one resample
        # at a slightly higher temperature recovers most of them.
        body["options"]["temperature"] = 0.6
    return None
