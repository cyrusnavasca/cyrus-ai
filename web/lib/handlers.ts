import type { Config } from "./config";
import { type Budget, type Counter, chargeBudget, checkIp, readBudget, refundBudget } from "./limits";
import { ModalError, type ModalClient } from "./modal";
import { signTicket, verifyTicket } from "./ticket";
import { validateHistory } from "./validate";

// Route logic, kept apart from the route files so it can be tested with fakes.
// Nothing here logs or echoes message text: only reasons, counts and statuses.

export type Deps = {
  config: Config;
  counter: Counter;
  modal: ModalClient;
  isBot: () => Promise<boolean>;
  now: () => Date;
  log: (event: string, fields?: Record<string, string | number | boolean>) => void;
};

// Six turns of 300 characters is under 4 KB of JSON; 16 KB leaves room for
// escaping and still refuses anything that is not a chat.
const MAX_BODY_BYTES = 16_384;

const json = (body: unknown, status = 200, headers?: HeadersInit) =>
  Response.json(body, { status, headers });

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function clientIp(req: Request): string {
  // On Vercel the first x-forwarded-for hop is the client; later hops are proxies.
  const first = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  return first || req.headers.get("x-real-ip")?.trim() || "unknown";
}

async function looksLikeBot(deps: Deps): Promise<boolean> {
  try {
    return await deps.isBot();
  } catch (e) {
    // Fail open: the budget caps are the real limit, and a BotID outage should
    // not take the page down with it.
    deps.log("send.bot_check_error", { error: errorText(e) });
    return false;
  }
}

export async function handleSend(req: Request, deps: Deps): Promise<Response> {
  if (await looksLikeBot(deps)) {
    deps.log("send.bot");
    return json({ error: "forbidden" }, 403);
  }

  const declared = Number(req.headers.get("content-length") ?? 0);
  if (declared > MAX_BODY_BYTES) return json({ error: "too large" }, 413);
  const raw = await req.text();
  if (raw.length > MAX_BODY_BYTES) return json({ error: "too large" }, 413);
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return json({ error: "invalid json" }, 400);
  }
  const v = validateHistory(body);
  if (!v.ok) return json({ error: v.error }, 400);

  // One instant for the charge and any refund (see chargeBudget).
  const now = deps.now();
  const { limits } = deps.config;
  let limited: "minute" | "day" | "budget" | null;
  try {
    const ip = await checkIp(deps.counter, clientIp(req), limits, now);
    limited = ip !== "ok" ? ip : (await chargeBudget(deps.counter, limits, now)) ? null : "budget";
  } catch (e) {
    // Fail closed: without the counters there is no budget, and the budget is
    // the only thing between a public page and the Modal bill.
    deps.log("send.limits_error", { error: errorText(e) });
    return json({ error: "unavailable" }, 503);
  }
  if (limited) {
    deps.log("send.limited", { reason: limited });
    return json({ reason: limited }, 429);
  }

  try {
    const id = await deps.modal.spawn(v.history);
    deps.log("send.ok", { turns: v.history.length, chars: v.history[v.history.length - 1].text.length });
    return json({ ticket: signTicket(id, deps.config.ticketSecret) });
  } catch (e) {
    if (e instanceof ModalError && e.status !== undefined) {
      try {
        await refundBudget(deps.counter, now);
      } catch (refundError) {
        deps.log("send.refund_error", { error: errorText(refundError) });
      }
    } else {
      // A timeout or dropped connection may still have spawned the GPU job, so the message stays charged.
      deps.log("send.spawn_ambiguous", { error: errorText(e) });
    }
    deps.log("send.modal_error", { error: errorText(e) });
    return json({ error: "upstream" }, 502);
  }
}

export async function handlePoll(req: Request, deps: Deps): Promise<Response> {
  const ticket = new URL(req.url).searchParams.get("ticket") ?? "";
  const id = verifyTicket(ticket, deps.config.ticketSecret);
  if (!id) return json({ error: "bad ticket" }, 400);
  try {
    const r = await deps.modal.result(id);
    if (r.pending) return json({ pending: true }, 202);
    return json({ reply: r.reply });
  } catch (e) {
    deps.log("poll.modal_error", { error: errorText(e) });
    return json({ error: "upstream" }, 502);
  }
}

const budgetCache = new WeakMap<Counter, { at: number; value: Budget }>();
const BUDGET_CACHE_MS = 10_000;

export async function handleBudget(deps: Deps): Promise<Response> {
  try {
    const now = deps.now();
    const hit = budgetCache.get(deps.counter);
    let b: Budget;
    if (hit && now.getTime() - hit.at < BUDGET_CACHE_MS) {
      b = hit.value;
    } else {
      b = await readBudget(deps.counter, deps.config.limits, now);
      budgetCache.set(deps.counter, { at: now.getTime(), value: b });
    }
    return json(b, 200, { "Cache-Control": "public, s-maxage=10" });
  } catch (e) {
    deps.log("budget.error", { error: errorText(e) });
    return json({ error: "unavailable" }, 503);
  }
}
