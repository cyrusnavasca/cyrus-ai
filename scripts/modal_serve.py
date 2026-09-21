"""A small web UI for prompting a trained adapter.

Served from Modal rather than run locally for two reasons: the page and the
model must share an origin for the browser to call it, and an 8B model does not
fit comfortably on a laptop.

  modal deploy scripts/modal_serve.py     # stable URL, survives this shell
  modal serve  scripts/modal_serve.py     # live-reloads while editing

Two pieces on purpose. Modal caps a web request at 150 seconds and then answers
with a 303 to a result URL, which a browser `fetch` cannot follow across
origins - it fails as "TypeError: Failed to fetch". Loading two 8B models on a
cold container can exceed that, so the page spawns the work and polls for it:

  Worker   GPU, holds the models, `scaledown_window` releases it when idle
  web      CPU only, spawns a Worker call and reports its status

A cold first answer takes a minute or two; the page shows a timer rather than
hanging, and an idle page costs nothing.

Access is gated on a token because this model writes in one specific person's
voice and is trained on their friends' messages. Set STYLE_FT_TOKEN before
deploying; the default is only good enough for a private link.
"""

# No `from __future__ import annotations` here: it turns route annotations into
# strings, and FastAPI cannot resolve `Request` because that import lives inside
# the method - every route then 422s with "query.request field required".
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

PAGE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>style-ft</title>
<style>
  :root { color-scheme: light dark; --bg:#fbfbfa; --fg:#1a1a1a; --mut:#6b6b6b;
          --line:#e0e0dd; --card:#fff; --accent:#2d5bff; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16161a; --fg:#ececea; --mut:#9a9a96; --line:#2c2c32; --card:#1e1e23; }
  }
  * { box-sizing:border-box }
  body { margin:0; padding:24px 16px 64px; background:var(--bg); color:var(--fg);
         font:15px/1.5 ui-sans-serif,-apple-system,system-ui,sans-serif }
  main { max-width:680px; margin:0 auto }
  h1 { font-size:17px; margin:0 0 2px; letter-spacing:-.01em }
  p.sub { color:var(--mut); margin:0 0 20px; font-size:13px }
  .row { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px }
  select, button, textarea, input {
    font:inherit; color:inherit; background:var(--card);
    border:1px solid var(--line); border-radius:8px; padding:9px 11px }
  textarea { width:100%; min-height:78px; resize:vertical }
  button { background:var(--accent); color:#fff; border-color:transparent;
           cursor:pointer; font-weight:550; padding:9px 18px }
  button:disabled { opacity:.5; cursor:default }
  .out { margin-top:20px }
  .lab { font-size:11px; text-transform:uppercase; letter-spacing:.07em;
         color:var(--mut); margin-bottom:5px }
  .bub { background:var(--card); border:1px solid var(--line); border-radius:12px;
         padding:12px 14px; white-space:pre-wrap; word-break:break-word;
         margin-bottom:14px; min-height:20px }
  .tuned { border-color:var(--accent) }
  .err { color:#c0392b }
  .hint { color:var(--mut); font-size:12px; margin-top:16px }
</style>
<main>
  <h1>style-ft</h1>
  <p class="sub">base Llama 3.1 vs your fine-tune, same prompt</p>

  <div class="row">
    <select id="run">__RUNS__</select>
    <input id="who" placeholder="who's texting you" value="a friend" style="flex:1;min-width:150px">
  </div>
  <textarea id="q" placeholder="type a message..."></textarea>
  <div class="row" style="margin-top:10px">
    <button id="go">Send</button>
    <span id="status" class="hint" style="align-self:center"></span>
  </div>

  <div class="out">
    <div class="lab">base model</div><div class="bub" id="base"></div>
    <div class="lab">your fine-tune</div><div class="bub tuned" id="tuned"></div>
  </div>
  <p class="hint">First request after idle takes ~60-90s while the model loads.
     After that it's about a second.</p>
</main>
<script>
const $ = id => document.getElementById(id);
const key = new URLSearchParams(location.search).get('k') || '';

// A chat run is given what someone said TO you; a style run restyles your own
// sentence - so the "who" field only means something for chat.
function syncWho() { $('who').style.display = $('run').value.startsWith('chat') ? '' : 'none'; }
$('run').onchange = syncWho; syncWho();

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function send() {
  const text = $('q').value.trim();
  if (!text) return;
  $('go').disabled = true;
  $('base').textContent = $('tuned').textContent = '';
  $('base').classList.remove('err'); $('tuned').classList.remove('err');
  const t0 = Date.now();
  const tick = setInterval(() => {
    $('status').textContent = 'waking the model... ' + ((Date.now() - t0) / 1000 | 0) + 's';
  }, 500);
  try {
    // Spawn, then poll. A single blocking request would hit Modal's 150s cap
    // and redirect to a result URL that fetch cannot follow.
    const r = await fetch('generate?k=' + encodeURIComponent(key), {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prompt: text, run: $('run').value, who: $('who').value})
    });
    const started = await r.json();
    if (!r.ok) throw new Error(started.detail || r.statusText);

    let out = null;
    for (let i = 0; i < 300 && !out; i++) {
      await sleep(2000);
      const p = await fetch('result?k=' + encodeURIComponent(key) +
                            '&id=' + encodeURIComponent(started.id));
      if (p.status === 202) continue;
      const d = await p.json();
      if (!p.ok) throw new Error(d.detail || p.statusText);
      out = d;
    }
    if (!out) throw new Error('timed out after 10 minutes');
    $('base').textContent = out.base; $('tuned').textContent = out.tuned;
    $('status').textContent = out.seconds.toFixed(1) + 's';
  } catch (e) {
    $('tuned').textContent = String(e); $('tuned').classList.add('err');
    $('status').textContent = '';
  } finally {
    clearInterval(tick);
    $('go').disabled = false;
  }
}
$('go').onclick = send;
$('q').addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') send();
});
</script>
"""


def runs() -> list:
    root = pathlib.Path("/vol/outputs")
    if not root.exists():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if (d / "adapter").exists() and (d / "training_config.json").exists()
    )


@app.cls(
    image=image,
    gpu="L4",
    volumes={"/vol": vol, "/root/.cache/huggingface": hf_cache},
    # Long enough that a testing session never re-pays the cold start, short
    # enough that a forgotten tab does not burn credit overnight.
    scaledown_window=600,
    timeout=1800,
)
class Worker:
    @modal.enter()
    def setup(self) -> None:
        import sys

        sys.path.insert(0, "/root")
        self.loaded: dict[str, object] = {}

    def _model(self, name: str):
        """Load and cache one model. Two 8B models fit an L4 together; a third
        would not, so the cache is capped at the base plus one adapter."""
        from unsloth import FastLanguageModel  # isort: skip

        if name in self.loaded:
            return self.loaded[name]
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=name, max_seq_length=2048, dtype=None, load_in_4bit=True
        )
        FastLanguageModel.for_inference(model)
        if len(self.loaded) >= 2:
            self.loaded.clear()
        self.loaded[name] = (model, tokenizer)
        return self.loaded[name]

    def _run(self, name: str, pair: dict, my_name: str) -> str:
        import torch
        from style_ft.formatting import messages_for

        model, tokenizer = self._model(name)
        text = tokenizer.apply_chat_template(
            messages_for(pair, include_response=False, my_name=my_name),
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = tokenizer(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(
                **inputs, max_new_tokens=96, do_sample=True, temperature=0.8,
                top_p=0.95, pad_token_id=tokenizer.eos_token_id,
            )
        return tokenizer.decode(
            out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
        ).strip()

    @modal.method()
    def generate(self, prompt: str, run: str, who: str) -> dict:
        import json
        import time

        cfg = json.loads(
            pathlib.Path(f"/vol/outputs/{run}/training_config.json").read_text()
        )
        my_name = cfg.get("my_name", "Me")
        pair = (
            {"context": [{"speaker": who, "text": prompt}], "output": "",
             "meta": {"with": who, "is_group": False}}
            if cfg.get("format") == "chat" else {"input": prompt, "output": ""}
        )
        started = time.time()
        base = self._run(cfg["base_model"], pair, my_name)
        tuned = self._run(f"/vol/outputs/{run}/adapter", pair, my_name)
        return {"base": base, "tuned": tuned, "seconds": time.time() - started}


# CPU only: this container just spawns work and reports on it, so it stays warm
# cheaply and never competes for a GPU.
@app.function(
    image=image, volumes={"/vol": vol}, scaledown_window=300, timeout=300
)
@modal.asgi_app()
def web():
    import modal as _modal
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse

    api = FastAPI()

    def check(request: Request) -> None:
        if request.query_params.get("k") != TOKEN:
            # This model writes as one specific person and was trained on their
            # friends' messages; an open endpoint is not acceptable.
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
        prompt = (body.get("prompt") or "").strip()
        run = body.get("run") or ""
        who = (body.get("who") or "a friend").strip() or "a friend"
        if not prompt or run not in runs():
            raise HTTPException(status_code=400, detail="unknown run or empty prompt")
        call = Worker().generate.spawn(prompt=prompt, run=run, who=who)
        return JSONResponse({"id": call.object_id})

    @api.get("/result")
    def result(request: Request):
        check(request)
        call_id = request.query_params.get("id", "")
        if not call_id:
            raise HTTPException(status_code=400, detail="missing id")
        try:
            # timeout=0 polls: it raises rather than waiting, so this request
            # always returns immediately and never approaches the 150s cap.
            return JSONResponse(_modal.FunctionCall.from_id(call_id).get(timeout=0))
        except TimeoutError:
            return JSONResponse({"pending": True}, status_code=202)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return api
