# Public chat UI on Vercel — design

Status: draft for review · 2026-10-01

A public web page, hosted on Vercel, where anyone can text the fine-tuned model.
It looks like an iPhone running iMessage, and the contact is **CyrusGPT**. The
model keeps running on Modal. Vercel serves the page and a thin proxy that holds
the secret and enforces a budget, because the Modal account has $30/month of
credit and a public page must not be able to spend more than that.

---

## 1. Goals and non-goals

**Goals**

- Anyone with the URL can text CyrusGPT with no account and no token.
- It looks and feels like iMessage on an iPhone: a phone frame on desktop and
  full screen on a real phone.
- Worst-case Modal spend is bounded by configuration, not by hoping traffic stays low.
- Visitors can see how much of the shared budget is left, before it runs out.
- `STYLE_FT_TOKEN` never reaches the browser.
- Deploys to Vercel from this repo with no manual build steps beyond env vars.

**Non-goals (v1)**

- User accounts, saved conversations on a server, or analytics on message content.
- A run picker. The public page serves one pinned adapter.
- Streaming tokens. The model returns a whole reply, and the typing indicator covers the wait.
- Reconciling against Modal's billing API (see §9, later).
- Replacing the existing token-gated Modal page. It keeps working for private use.

---

## 2. Architecture

```
 browser (Vercel page)                Vercel route handlers              Modal (existing app)
 ─────────────────────                ─────────────────────              ────────────────────
 POST /api/send {history} ──────────▶ bot check
                                      validate + clamp
                                      per-IP limit      ──Redis──┐
                                      global budget     ──Redis──┤
                                      POST /generate  ───────────┼────▶ web: spawn Worker.reply
                                      ◀── {id}                   │      (CPU, cheap, always fine)
 ◀── {ticket}                                                    │
                                                                 │
 GET /api/poll?ticket=… (every 1.5s)  verify ticket HMAC         │
                                      GET /result?id=…  ─────────┼────▶ web: FunctionCall.get(0)
 ◀── 202 pending | {reply}                                       │      Worker (L4 GPU, max 1)
                                                                 │
 GET /api/budget (on load + after     read counters   ──Redis────┘
   each reply)
 ◀── {today:{used,cap}, month:{used,cap}}
```

Three pieces, each with one job:

| Unit | Lives in | Job | Depends on |
|---|---|---|---|
| Chat page | `web/app/`, `web/components/` | iPhone/iMessage UI, holds the conversation in the browser | `/api/*` only |
| Proxy routes | `web/app/api/*` | Secret holding, validation, limits, budget accounting | Upstash Redis, Modal URL |
| Modal app | `deploy/serve.py` | GPU inference (unchanged shape), now with header auth and hard input caps | the adapter volume |

Spawn-then-poll stays as it is. Modal caps a web request at 150s, and a cold 8B
load can take longer than that. Vercel functions also have time limits, so no
single request in the chain waits on the GPU.

---

## 3. The page: iPhone + iMessage

### Layout

- **Desktop (≥ 500px wide):** a phone frame centred on a neutral background. It
  has rounded corners, a Dynamic Island, and a fixed 390×844 viewport inside
  it. CSS only, no images.
- **Phone (< 500px):** no frame. The chat fills the screen, uses `100dvh`, and
  respects `env(safe-area-inset-*)`, so it looks native on an actual iPhone.

### Elements, top to bottom

1. **Status bar** (desktop frame only; a real phone draws its own): the time
   (live, local), signal and wifi glyphs, and a **battery**. The battery is the
   budget meter (§6).
2. **Conversation header:** a back chevron (decorative), a centred circular
   avatar, and below it **CyrusGPT ›** in small text, as in iOS 17+.
   - The avatar is `web/public/avatar.png`. v1 ships the generic grey iOS
     silhouette as that file, so swapping in the Photoshopped image later means
     replacing one file with no code change.
3. **Message log:**
   - Visitor messages: blue (`#0B84FE`) bubbles, right-aligned, with a tail on
     the last bubble of a run.
   - CyrusGPT messages: grey bubbles (`#E9E9EB` light / `#262628` dark),
     left-aligned, with a tail.
   - Consecutive bubbles from one side are tightly grouped. Only the last one
     gets a tail.
   - A centred timestamp header ("Today 9:41 PM") appears at the start, and
     again after any gap over 15 minutes.
   - "Delivered" appears under the visitor's latest message once the send is
     accepted. "Read" is never shown.
   - The **typing indicator** is a grey bubble with three animated dots while
     polling.
4. **Input bar:** a "+" button (decorative, disabled), a rounded pill field
   with placeholder **iMessage**, and a blue circular ↑ send button that appears
   only when there is text. Enter sends and Shift+Enter makes a new line.
   Input is capped at 300 characters, with a counter after 250.

### States

| State | What the visitor sees |
|---|---|
| Waiting, < 12s | Typing dots |
| Waiting, ≥ 12s (cold start) | Typing dots, plus a small grey caption under them: "CyrusGPT is waking up…" |
| Send failed (network or 5xx) | Red "!" beside the bubble and "Not Delivered", as iMessage does; tap to retry |
| Per-IP limit hit (429) | Centred grey system note: "You've hit today's limit — try again tomorrow" (or "slow down a sec" for the per-minute limit) |
| Budget exhausted | Battery empty and red. The input is replaced by a grey note: "CyrusGPT's phone died 🪫 Back tomorrow." |
| Bot check failed | Same as a generic send failure. No detail is leaked. |

### Behaviour

- The conversation lives in browser state and is mirrored to `localStorage` (in
  `try/catch`), so a refresh keeps the thread. A "Clear" option sits in the
  header's long-press or overflow menu.
- Only the last 6 turns are sent each time. That matches `CONTEXT_TURNS`, and
  the model was trained on no more.
- Light and dark mode follow `prefers-color-scheme`, using iOS's palettes.
- A one-line footer outside the phone frame (desktop) or in the empty state
  (phone) says: "AI trained on my texts. It's not me. Don't share anything private."

---

## 4. Proxy routes (Vercel, Node runtime)

All routes are App Router route handlers under `web/app/api/`. Every response
is JSON. None of them log message text, only lengths, status codes, and timing.

### `POST /api/send`

Request: `{ history: [{ me: boolean, text: string }] }`

Steps, in order (the first failure wins):

1. **Bot check** (Vercel BotID). Failure returns 403.
2. **Validate:** `history` is a non-empty array, and the last item has
   `me: true`. Trim to the last 6 items. Each `text` is a string, and the
   newest one is 1–300 chars after trimming, with older ones clamped to 300.
   Failure returns 400.
3. **Per-IP limits** (sliding window, keyed on `x-forwarded-for`'s first hop):
   `PER_IP_PER_MINUTE` (default 5) and `PER_IP_PER_DAY` (default 20). Failure
   returns 429 with `{ reason: "minute" | "day" }`.
4. **Global budget:** atomically increment `budget:day:<YYYY-MM-DD UTC>` and
   `budget:month:<YYYY-MM>`. If either is over its cap, decrement both back and
   return 429 with `{ reason: "budget" }`. Keys expire after 2 days and 32 days.
5. **Spawn:** POST to `${MODAL_BASE_URL}/generate` with
   `Authorization: Bearer ${STYLE_FT_TOKEN}` and body
   `{ history, run: STYLE_FT_RUN }`. On a non-2xx, refund the budget counters
   and return 502.
6. Return `{ ticket }`. The ticket is `<modal call id>.<HMAC-SHA256(call id, TICKET_SECRET)>`.
   The browser never gets a bare call id, and `/api/poll` refuses anything it
   did not issue.

Budget is charged at spawn, not at completion. A reply that later fails still
cost GPU time, so it should count.

### `GET /api/poll?ticket=…`

Verify the HMAC (failure returns 400), then GET `${MODAL_BASE_URL}/result?id=…`
with the bearer header. Modal's 202 becomes 202. Its 200 passes `{ reply }`
through. Anything else becomes 502. Polling is not rate-limited per IP beyond
Vercel's defaults, because it costs no GPU. The client stops after 300 polls
(about 7.5 min).

### `GET /api/budget`

Returns:

```json
{ "today": { "used": 87, "cap": 120 }, "month": { "used": 1410, "cap": 3000 } }
```

It reads two Redis keys and nothing else. It is cacheable for 10s
(`s-maxage=10`), so a busy page doesn't hammer Redis.

### Shared modules

- `web/lib/modal.ts`: the two Modal calls. This is the only file that knows
  the Modal URL or the token.
- `web/lib/limits.ts`: per-IP and global budget logic over an injected Redis
  client, so it can be tested without a network.
- `web/lib/ticket.ts`: HMAC sign and verify.
- `web/lib/validate.ts`: request shape and clamping.

---

## 5. Changes to `deploy/serve.py`

The public page now reaches Modal through the proxy, so the Modal side must be
safe against any request the proxy might forward, and against anyone who finds
the Modal URL directly.

1. **Auth via header.** `check()` accepts `Authorization: Bearer <token>` as
   well as the existing `?k=` (kept, so the private page still works). Use a
   constant-time compare (`hmac.compare_digest`).
2. **Hard input caps, server-side.** Trim `history` to `CONTEXT_TURNS`, clamp
   each text to 300 chars, and return 400 if the total exceeds 2,000 chars.
   Today it accepts any length.
3. **Clamp sampling params.** `temperature` is clamped to [0.3, 0.8]. Today any
   float from the body goes straight to `generate`. Accept `partner` only from
   a fixed allow-list (`DEFAULT_PARTNER`), otherwise ignore it.
4. **Cost caps on the GPU:**
   - `Worker`: `max_containers=1`, so a spike queues instead of renting more L4s.
   - `scaledown_window`: 600s → **120s**. The cold start returns more often,
     but idle burn drops 5×, and on a $30 budget idle time is the main cost.
5. **Output redaction:** before returning, the reply passes through a scrub
   that replaces phone numbers and email addresses with `[redacted]`. This is a
   backstop for the memorization risk (§8), not the fix.

Items 2, 3, and 5 are pure functions in a new stdlib-only module,
`src/style_ft/serving/guard.py`, so they get unit tests like the rest of
`src/`. `serve.py` imports it (the module is already shipped to the image with
`add_local_dir`).

---

## 6. Budget meter (the battery)

The status-bar battery shows **today's remaining budget**:
`1 − today.used / today.cap`.

- It is green above 20%, yellow from 20% down to 10%, and red below 10%.
  These are iOS's colours, so the meaning reads without explanation.
- Tapping or hovering the battery opens a small popover: "87 / 120 texts used
  today · resets at midnight UTC · shared by everyone". It also shows the
  month line once the month is over 50% used.
- On a phone there is no fake status bar, so the same meter appears as a
  small pill under the contact name: "🔋 27% left today".
- It refreshes on load and after each reply. It does not poll on a timer.

**Why message counts and not dollars.** The number comes from our own Redis
counters. They update instantly and don't depend on Modal's billing API, which
reports spend after the fact and has no remaining-credit endpoint.

### Sizing the caps

The defaults below are derived and written down so they can be re-derived when
prices change. Verify the L4 rate on Modal's pricing page before launch.

- Assumed L4 ≈ $0.80/hr, about $0.00022/s.
- A warm reply is about 3–6s of GPU time. A cold start adds about 30–60s,
  plus up to 120s of idle tail after the last message.
- The worst case per isolated message (cold load + reply + full idle tail) is
  about 190s, or $0.04. Messages inside a warm session cost far less.
- `MONTHLY_MESSAGE_CAP = 3000` and `DAILY_MESSAGE_CAP = 120`. 120 worst-case
  messages a day is about $5. That's pessimistic, because most messages land
  in warm sessions. These are starting values. Tune them after a week of real
  traffic by comparing Modal's billing page to the counters.
- **Also set a hard spending limit in Modal's dashboard** (Settings → Usage /
  Billing) below $30. That is the last line of defence if every other limit is
  wrong.

---

## 7. Repository and deployment

```
web/                        Next.js (App Router, TypeScript), Vercel root directory
  app/
    page.tsx                the phone
    api/send/route.ts
    api/poll/route.ts
    api/budget/route.ts
  components/               Phone, StatusBar, Battery, ConversationHeader,
                            MessageList, Bubble, TypingIndicator, InputBar
  lib/                      modal.ts, limits.ts, ticket.ts, validate.ts
  public/avatar.png         blank silhouette; replace to change the pfp
  .env.example              every variable below, with placeholder values
src/style_ft/serving/guard.py   input caps, clamping, redaction (stdlib)
tests/test_serving.py
deploy/serve.py             changes from §5
```

- Styling is plain CSS modules, with no UI framework. The look is bespoke
  anyway, and it keeps dependencies to `next`, `react`, `@upstash/redis`,
  `@upstash/ratelimit`, and `botid`.
- **Vercel project:** Root Directory = `web`, framework preset Next.js. Add
  Upstash Redis from the Vercel Marketplace, which injects
  `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`.
- **Env vars (Vercel):**

  | Name | Secret | Default |
  |---|---|---|
  | `MODAL_BASE_URL` | no | none |
  | `STYLE_FT_TOKEN` | yes | none (required) |
  | `STYLE_FT_RUN` | no | none (e.g. `chat-v3`) |
  | `TICKET_SECRET` | yes | none |
  | `DAILY_MESSAGE_CAP` | no | 120 |
  | `MONTHLY_MESSAGE_CAP` | no | 3000 |
  | `PER_IP_PER_MINUTE` | no | 5 |
  | `PER_IP_PER_DAY` | no | 20 |

  Missing required vars make the routes return 503 with a clear server log.
  They never fall back to a default secret, in line with the existing rule in
  `serve.py`.
- **CI:** add a `web` job to `.github/workflows/ci.yml` that runs `npm ci`,
  `lint`, `tsc --noEmit`, `vitest run`, and `next build`.

---

## 8. Privacy gate (before the URL is shared)

Going public means strangers can probe a model trained on friends' real
messages. Before launch:

1. **Probe script** `tools/probe_leaks.py`: sends about 50 prompts designed to
   elicit memorized content ("what's your number", "who's [common first
   name]", "send me the address", "what did you text last night") to the
   pinned run at the serving temperature, 5 samples each. It writes the
   replies to `outputs/` (gitignored) for a manual read.
2. **Read it.** If replies contain real names, numbers, addresses, or
   identifiable private details beyond what the redaction catches, don't
   launch. Fix the data first (curation or name pseudonymization in `pairs`)
   and retrain.
3. The redaction in §5 stays on regardless.

Launch is blocked on step 2 being done and recorded (a one-line note in the PR).

---

## 9. Testing

- **`tests/test_serving.py`** (stdlib `unittest`, as in the existing suite):
  history trimming, char caps, the total cap, temperature clamping, the partner
  allow-list, and redaction of US and international phone formats and emails
  (including a few false-positive checks: times like "9:41", prices, years).
- **`web/lib/*.test.ts`** (Vitest):
  - `validate`: shapes, clamping, rejections.
  - `limits`: a fake Redis covering per-IP minute and day, the global
    day/month caps, refund on spawn failure, and refund when over cap.
  - `ticket`: round trip, tampered id, tampered signature.
  - Route handlers with `modal.ts` mocked: the happy path, each 4xx/5xx
    branch, and confirmation that no response body or log line contains
    message text.
- **Manual pre-launch check:** a cold-start send on a real iPhone (Safari) and
  on desktop Chrome; the budget-exhausted state by setting `DAILY_MESSAGE_CAP=1`
  on a preview deployment; dark mode.

**Later (not v1):** a daily cron that calls `modal.billing.workspace_billing_report()`
and logs real spend next to the counter totals, to calibrate the per-message estimate.

---

## 10. Decisions taken by default — override any of these

1. The battery shows **today's** budget, not the month's. The month is the
   backstop and appears in the popover.
2. Limits: **20/day and 5/min per IP; 120/day and 3000/month globally.**
3. `scaledown_window` goes to **120s**: cheaper, but more cold starts.
4. The conversation persists in **localStorage** and is never stored server-side.
5. Bot protection uses **Vercel BotID**. If it isn't available on the Hobby
   plan at build time, fall back to Cloudflare Turnstile.
6. The web app lives in **`web/`** in this repo, not a separate repo.
7. The disclaimer line ("AI trained on my texts. It's not me…") is on the page.
