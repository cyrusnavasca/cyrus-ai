"""Text the fine-tune.

A chat page: you type, it replies as the person the adapter was trained on. The
page keeps the conversation and sends it back each turn, because the model was
trained on several turns of context and gets vague with only one line - that is
the page's job, not something to ask the user to format.

  modal deploy scripts/modal_serve.py     # stable URL, survives this shell
  modal serve  scripts/modal_serve.py     # live-reloads while editing

Two pieces on purpose. Modal caps a web request at 150 seconds and then answers
with a 303 to a result URL, which a browser fetch cannot follow across origins
(it fails as "TypeError: Failed to fetch"). Loading an 8B model on a cold
container can exceed that, so the page spawns the work and polls for it:

  Worker   GPU, holds the model, scaledown_window releases it when idle
  web      CPU only, spawns a Worker call and reports its status

Access is gated on a token: this model writes in one specific person's voice and
was trained on their friends' messages. Set STYLE_FT_TOKEN before deploying.
"""

# No `from __future__ import annotations`: it turns route annotations into
# strings, and FastAPI cannot resolve `Request` because that import lives inside
# the function - every route then 422s with "query.request field required".
import os
import pathlib

import modal

REPO = pathlib.Path(__file__).resolve().parents[1]

app = modal.App("style-ft-ui")

vol = modal.Volume.from_name("style-ft")
hf_cache = modal.Volume.from_name("style-ft-hf-cache", create_if_missing=True)

image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .pip_install("unsloth", "unsloth_zoo", "trl", "peft", "datasets",
                 "bitsandbytes", "accelerate", "fastapi[standard]")
    .add_local_dir(REPO / "src" / "style_ft", "/root/style_ft")
)

TOKEN = os.environ.get("STYLE_FT_TOKEN", "cyrus-dev")

# Training never saw the label "a friend" - every conversation was with an
# aliased handle. Using one the model actually met keeps inference in
# distribution; this is the alias of the thread that dominates the corpus.
# Who the model is told it is texting. This matters more than it looks: the
# alias picks which relationship's register the adapter falls into, and one
# thread is 58% of the training pairs. Naming that thread makes every reply
# read like a message to a partner ("morning love!!") regardless of what was
# typed. Friend 1 is the largest ordinary-friend DM, which is the neutral
# register a stranger typing into this page expects.
DEFAULT_PARTNER = "Friend 1"

# The context window the pairs were built with. Sending more turns than the
# model was trained on degrades it rather than helping.
TEMPERATURE = 0.5
CONTEXT_TURNS = 6

PAGE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>style-ft</title>
<style>
  :root { color-scheme: light dark; --bg:#fbfbfa; --fg:#1a1a1a; --mut:#8a8a86;
          --line:#e3e3e0; --them:#ececea; --me:#2d7bff; --meFg:#fff; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#131316; --fg:#ececea; --mut:#87877f; --line:#2a2a30; --them:#26262c; }
  }
  * { box-sizing:border-box }
  html, body { height:100% }
  body { margin:0; background:var(--bg); color:var(--fg); display:flex;
         flex-direction:column;
         font:16px/1.45 ui-sans-serif,-apple-system,system-ui,sans-serif }
  header { display:flex; align-items:center; gap:10px; padding:10px 14px;
           border-bottom:1px solid var(--line); flex:0 0 auto }
  header b { font-size:14px; font-weight:600 }
  select, button, textarea { font:inherit; color:inherit }
  select { background:transparent; border:1px solid var(--line); border-radius:7px;
           padding:4px 7px; font-size:13px }
  #reset { margin-left:auto; background:none; border:none; color:var(--mut);
           font-size:13px; cursor:pointer }
  #log { flex:1 1 auto; overflow-y:auto; padding:16px 14px 8px;
         display:flex; flex-direction:column; gap:8px }
  .msg { max-width:min(78%,560px); padding:9px 13px; border-radius:18px;
         white-space:pre-wrap; word-wrap:break-word }
  .them { background:var(--them); align-self:flex-start; border-bottom-left-radius:5px }
  .me { background:var(--me); color:var(--meFg); align-self:flex-end;
        border-bottom-right-radius:5px }
  .meta { align-self:center; color:var(--mut); font-size:12px; padding:2px }
  .err { align-self:center; color:#d05; font-size:13px; text-align:center }
  footer { flex:0 0 auto; display:flex; gap:8px; padding:10px 14px;
           padding-bottom:calc(10px + env(safe-area-inset-bottom,0px));
           border-top:1px solid var(--line) }
  textarea { flex:1; resize:none; min-height:42px; max-height:140px;
             background:var(--bg); border:1px solid var(--line);
             border-radius:20px; padding:10px 14px }
  #send { background:var(--me); color:var(--meFg); border:none; border-radius:20px;
          padding:0 18px; font-weight:600; cursor:pointer }
  #send:disabled { opacity:.45; cursor:default }
</style>
<header>
  <b>style-ft</b>
  <select id="run">__RUNS__</select>
  <button id="reset">clear</button>
</header>
<div id="log"></div>
<footer>
  <textarea id="q" rows="1" placeholder="Message"></textarea>
  <button id="send">Send</button>
</footer>
<script>
const $ = id => document.getElementById(id);
const key = new URLSearchParams(location.search).get('k') || '';
let history = [];   // {me:bool, text:string} - "me" is the person typing here
let busy = false;

function bubble(cls, text) {
  const d = document.createElement('div');
  d.className = cls; d.textContent = text;
  $('log').appendChild(d);
  $('log').scrollTop = $('log').scrollHeight;
  return d;
}
function redraw() {
  $('log').innerHTML = '';
  for (const m of history) bubble('msg ' + (m.me ? 'me' : 'them'), m.text);
}
$('reset').onclick = () => { history = []; redraw(); };
$('run').onchange = () => { history = []; redraw(); };

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function send() {
  const text = $('q').value.trim();
  if (!text || busy) return;
  busy = true; $('send').disabled = true;
  $('q').value = ''; $('q').style.height = 'auto';
  history.push({me: true, text});
  redraw();

  const t0 = Date.now();
  const note = bubble('meta', 'typing...');
  const tick = setInterval(() => {
    const s = (Date.now() - t0) / 1000 | 0;
    note.textContent = s > 12 ? 'waking the model... ' + s + 's' : 'typing...';
  }, 500);

  try {
    // Spawn then poll: one blocking request would hit Modal's 150s cap and
    // redirect somewhere fetch cannot follow.
    const r = await fetch('generate?k=' + encodeURIComponent(key), {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({history, run: $('run').value})
    });
    const started = await r.json();
    if (!r.ok) throw new Error(started.detail || r.statusText);

    let out = null;
    for (let i = 0; i < 300 && !out; i++) {
      await sleep(1500);
      const p = await fetch('result?k=' + encodeURIComponent(key) +
                            '&id=' + encodeURIComponent(started.id));
      if (p.status === 202) continue;
      const d = await p.json();
      if (!p.ok) throw new Error(d.detail || p.statusText);
      out = d;
    }
    if (!out) throw new Error('timed out');
    history.push({me: false, text: out.reply});
    redraw();
  } catch (e) {
    redraw();
    bubble('err', String(e));
  } finally {
    clearInterval(tick);
    busy = false; $('send').disabled = false; $('q').focus();
  }
}

$('send').onclick = send;
$('q').addEventListener('input', e => {
  e.target.style.height = 'auto';
  e.target.style.height = Math.min(e.target.scrollHeight, 140) + 'px';
});
$('q').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
$('q').focus();
</script>
"""


def runs() -> list:
    """Finished adapters that can hold a conversation, newest first.

    Style-transfer adapters are deliberately excluded. They were trained on
    (sentence -> same sentence in my voice) and have no notion of a reply, so
    served here they parrot whatever you typed back at you in the right voice -
    which reads as a broken chat model rather than the restyler it is. They
    also sort above the chat runs by name, so leaving them in the list made
    "style-v2" the default the page opened on.
    """
    import json

    root = pathlib.Path("/vol/outputs")
    if not root.exists():
        return []
    names = []
    for d in sorted(root.iterdir(), reverse=True):
        cfg = d / "training_config.json"
        if not (d / "adapter").exists() or not cfg.exists():
            continue  # still training, or died before writing the adapter
        try:
            if json.loads(cfg.read_text()).get("format") == "chat":
                names.append(d.name)
        except (OSError, ValueError):
            continue
    return names


@app.cls(
    image=image,
    gpu="L4",
    volumes={"/vol": vol, "/root/.cache/huggingface": hf_cache},
    # Long enough that a conversation never re-pays the cold start, short enough
    # that a forgotten tab does not burn credit overnight.
    scaledown_window=600,
    timeout=1800,
)
class Worker:
    @modal.enter()
    def setup(self) -> None:
        import sys

        sys.path.insert(0, "/root")
        self.loaded: dict = {}

    def _model(self, name: str):
        from unsloth import FastLanguageModel  # isort: skip

        if name in self.loaded:
            return self.loaded[name]
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=name, max_seq_length=2048, dtype=None, load_in_4bit=True
        )
        FastLanguageModel.for_inference(model)
        # Two 8B models fit an L4; a third does not.
        if len(self.loaded) >= 2:
            self.loaded.clear()
        self.loaded[name] = (model, tokenizer)
        return self.loaded[name]

    @modal.method()
    def reply(self, history: list, run: str, partner: str = DEFAULT_PARTNER,
              temperature: float = TEMPERATURE) -> dict:
        import json
        import time

        import torch
        from style_ft.formatting import messages_for

        cfg = json.loads(
            pathlib.Path(f"/vol/outputs/{run}/training_config.json").read_text()
        )
        my_name = cfg.get("my_name", "Me")

        # The person typing in the page is the other party, so their messages
        # are the incoming side and the model supplies the replies. Only chat
        # adapters reach here; see runs().
        turns = [
            {"speaker": partner if m.get("me") else my_name,
             "text": m.get("text", "")}
            for m in history[-CONTEXT_TURNS:]
        ]
        pair = {"context": turns, "output": "",
                "meta": {"with": partner, "is_group": False}}

        started = time.time()
        model, tokenizer = self._model(f"/vol/outputs/{run}/adapter")
        text = tokenizer.apply_chat_template(
            messages_for(pair, include_response=False, my_name=my_name),
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = tokenizer(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(
                # Well below the usual 0.7. Texting is a
                # high-entropy target (base ppl 120, tuned 20.6 held out), so
                # the reply distribution is genuinely flat and 0.7 samples deep
                # enough into it to pick a fluent message about something else:
                # asked "have you heard from joe?" it answers "are u going to
                # his game?" or "u want coffee". Sharpening keeps it on topic
                # and, counter-intuitively, yields MORE emoji - they are
                # frequent in the real replies, so the mode moves toward them.
                # 0.35 never wanders but goes flat ("yea", "okok"); 0.5 keeps
                # the voice. Overridable per request for retuning.
                **inputs, max_new_tokens=96, do_sample=True, temperature=temperature,
                top_p=0.9, repetition_penalty=1.1,
                pad_token_id=tokenizer.eos_token_id,
            )
        reply = tokenizer.decode(
            out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
        ).strip()
        return {"reply": reply, "seconds": time.time() - started}


# CPU only: this container just spawns work and reports on it, so it stays warm
# cheaply and never competes for a GPU.
@app.function(image=image, volumes={"/vol": vol}, scaledown_window=300, timeout=300)
@modal.asgi_app()
def web():
    import modal as _modal
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse

    api = FastAPI()

    def check(request: Request) -> None:
        if request.query_params.get("k") != TOKEN:
            raise HTTPException(status_code=401, detail="bad or missing ?k= token")

    @api.get("/", response_class=HTMLResponse)
    def index(request: Request):
        check(request)
        options = "".join(f"<option>{r}</option>" for r in runs()) or "<option>none</option>"
        return PAGE.replace("__RUNS__", options)

    @api.post("/generate")
    async def generate(request: Request):
        check(request)
        body = await request.json()
        history = body.get("history") or []
        run = body.get("run") or ""
        if not history or run not in runs():
            raise HTTPException(status_code=400, detail="unknown run or empty history")
        partner = body.get("partner") or DEFAULT_PARTNER
        temperature = float(body.get("temperature") or TEMPERATURE)
        call = Worker().reply.spawn(history=history, run=run, partner=partner,
                                    temperature=temperature)
        return JSONResponse({"id": call.object_id})

    @api.get("/result")
    def result(request: Request):
        check(request)
        call_id = request.query_params.get("id", "")
        if not call_id:
            raise HTTPException(status_code=400, detail="missing id")
        try:
            # timeout=0 polls rather than waiting, so this request returns
            # immediately and never approaches the 150s cap.
            return JSONResponse(_modal.FunctionCall.from_id(call_id).get(timeout=0))
        except TimeoutError:
            return JSONResponse({"pending": True}, status_code=202)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return api
