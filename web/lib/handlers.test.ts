import { describe, expect, it, vi } from "vitest";
import { loadConfig } from "./config";
import { type Deps, handleBudget, handlePoll, handleSend } from "./handlers";
import { type Counter, budgetKeys, readBudget } from "./limits";
import { memoryCounter } from "./memoryCounter";
import { ModalError, type ModalClient } from "./modal";
import { signTicket } from "./ticket";
import type { Turn } from "./validate";

const ENV = {
  MODAL_BASE_URL: "https://m.test",
  STYLE_FT_TOKEN: "tok",
  STYLE_FT_RUN: "chat-v3",
  TICKET_SECRET: "secret",
};
const NOW = new Date("2026-10-01T12:00:00Z");
const SECRET_TEXT = "my private message 4155550123";

function setup(opts: { env?: Record<string, string>; deps?: Partial<Deps> } = {}) {
  const logs: Array<{ event: string; fields: Record<string, unknown> }> = [];
  const spawned: Turn[][] = [];
  const modal: ModalClient = {
    async spawn(history) {
      spawned.push(history);
      return "fc-123";
    },
    async result() {
      return { pending: false, reply: "lol ok" };
    },
  };
  const deps: Deps = {
    config: loadConfig({ ...ENV, ...opts.env }),
    counter: memoryCounter(),
    modal,
    isBot: async () => false,
    now: () => NOW,
    log: (event, fields = {}) => logs.push({ event, fields }),
    ...opts.deps,
  };
  return { deps, logs, spawned };
}

function sendReq(body: unknown, headers: Record<string, string> = {}) {
  return new Request("https://x.test/api/send", {
    method: "POST",
    headers: { "content-type": "application/json", "x-forwarded-for": "1.2.3.4", ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

const hi = { history: [{ me: true, text: "hi" }] };
const used = async (deps: Deps, at = NOW) => readBudget(deps.counter, deps.config.limits, at);

describe("handleSend", () => {
  it("validates, charges, spawns and returns a signed ticket", async () => {
    const { deps, spawned } = setup();
    const res = await handleSend(sendReq(hi), deps);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ticket: signTicket("fc-123", "secret") });
    expect(spawned).toEqual([[{ me: true, text: "hi" }]]);
    expect((await used(deps)).today.used).toBe(1);
  });

  it("blocks bots before doing anything", async () => {
    const { deps, spawned } = setup({ deps: { isBot: async () => true } });
    expect((await handleSend(sendReq(hi), deps)).status).toBe(403);
    expect(spawned).toEqual([]);
    expect((await used(deps)).today.used).toBe(0);
  });

  it("fails open if the bot check itself errors, and logs it", async () => {
    const { deps, logs } = setup({
      deps: { isBot: async () => { throw new Error("botid down"); } },
    });
    expect((await handleSend(sendReq(hi), deps)).status).toBe(200);
    expect(logs.map((l) => l.event)).toContain("send.bot_check_error");
  });

  it.each([
    ["not json", "{"],
    ["wrong shape", { history: "hi" }],
    ["numbers as turns", { history: [1, 2, 3] }],
    ["visitor did not speak last", { history: [{ me: false, text: "yo" }] }],
  ])("rejects %s with 400 and spawns nothing", async (_name, body) => {
    const { deps, spawned } = setup();
    expect((await handleSend(sendReq(body), deps)).status).toBe(400);
    expect(spawned).toEqual([]);
  });

  it("rejects an oversized body with 413", async () => {
    const { deps, spawned } = setup();
    const history = Array.from({ length: 10_000 }, () => ({ me: true, text: "x".repeat(10) }));
    expect((await handleSend(sendReq({ history }), deps)).status).toBe(413);
    expect(spawned).toEqual([]);
  });

  it("applies the per-IP minute limit on the first forwarded hop", async () => {
    const { deps } = setup();
    for (let i = 0; i < 5; i++) {
      const r = await handleSend(sendReq(hi, { "x-forwarded-for": `9.9.9.9, 10.0.0.${i}` }), deps);
      expect(r.status).toBe(200);
    }
    const res = await handleSend(sendReq(hi, { "x-forwarded-for": "9.9.9.9, 10.0.0.99" }), deps);
    expect(res.status).toBe(429);
    expect(await res.json()).toEqual({ reason: "minute" });
  });

  it("refuses once the global daily budget is spent", async () => {
    const { deps } = setup({ env: { DAILY_MESSAGE_CAP: "1" } });
    expect((await handleSend(sendReq(hi, { "x-forwarded-for": "1.1.1.1" }), deps)).status).toBe(200);
    const res = await handleSend(sendReq(hi, { "x-forwarded-for": "2.2.2.2" }), deps);
    expect(res.status).toBe(429);
    expect(await res.json()).toEqual({ reason: "budget" });
  });

  it("refunds the budget when Modal fails", async () => {
    const { deps } = setup({
      deps: { modal: { spawn: async () => { throw new ModalError("generate returned 500", 500); }, result: async () => ({ pending: true }) } },
    });
    expect((await handleSend(sendReq(hi), deps)).status).toBe(502);
    expect((await used(deps)).today.used).toBe(0);
  });

  it("keeps the charge when the spawn outcome is unknown", async () => {
    const { deps } = setup({
      deps: { modal: { spawn: async () => { throw new DOMException("timeout", "TimeoutError"); }, result: async () => ({ pending: true }) } },
    });
    expect((await handleSend(sendReq(hi), deps)).status).toBe(502);
    expect((await used(deps)).today.used).toBe(1);
  });

  it("refunds the same day it charged when the failure straddles midnight UTC", async () => {
    const before = new Date("2026-10-01T23:59:59.900Z");
    const after = new Date("2026-10-02T00:00:00.100Z");
    const now = vi.fn().mockReturnValueOnce(before).mockReturnValue(after);
    const { deps } = setup({
      deps: {
        now,
        modal: { spawn: async () => { throw new ModalError("generate returned 500", 500); }, result: async () => ({ pending: true }) },
      },
    });
    await handleSend(sendReq(hi), deps);
    expect((await used(deps, before)).today.used).toBe(0);
    expect(await deps.counter.get("budget:day:2026-10-02")).toBeNull();
  });

  it("fails closed with 503 when the counter store is down", async () => {
    const down: Counter = {
      incr: async () => { throw new Error("redis down"); },
      decr: async () => { throw new Error("redis down"); },
      expire: async () => { throw new Error("redis down"); },
      get: async () => { throw new Error("redis down"); },
    };
    const { deps, spawned } = setup({ deps: { counter: down } });
    expect((await handleSend(sendReq(hi), deps)).status).toBe(503);
    expect(spawned).toEqual([]);
  });

  it("never puts message text in logs or error bodies", async () => {
    const ok = setup();
    await handleSend(sendReq({ history: [{ me: true, text: SECRET_TEXT }] }), ok.deps);
    const failing = setup({
      deps: { modal: { spawn: async () => { throw new Error("boom"); }, result: async () => ({ pending: true }) } },
    });
    const res = await handleSend(sendReq({ history: [{ me: true, text: SECRET_TEXT }] }), failing.deps);
    const all = JSON.stringify([ok.logs, failing.logs, await res.text()]);
    expect(all).not.toContain("private message");
    expect(all).not.toContain("4155550123");
  });
});

describe("handlePoll", () => {
  const pollReq = (ticket: string) =>
    new Request(`https://x.test/api/poll?ticket=${encodeURIComponent(ticket)}`);

  it("passes pending through as 202", async () => {
    const { deps } = setup({
      deps: { modal: { spawn: async () => "fc-1", result: async () => ({ pending: true }) } },
    });
    const res = await handlePoll(pollReq(signTicket("fc-1", "secret")), deps);
    expect(res.status).toBe(202);
  });

  it("returns the reply", async () => {
    const { deps } = setup();
    const res = await handlePoll(pollReq(signTicket("fc-1", "secret")), deps);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ reply: "lol ok" });
  });

  it("rejects a ticket it did not sign, without calling Modal", async () => {
    const result = vi.fn();
    const { deps } = setup({ deps: { modal: { spawn: async () => "x", result } } });
    expect((await handlePoll(pollReq(signTicket("fc-1", "wrong")), deps)).status).toBe(400);
    expect((await handlePoll(new Request("https://x.test/api/poll"), deps)).status).toBe(400);
    expect(result).not.toHaveBeenCalled();
  });

  it("maps a Modal failure to 502", async () => {
    const { deps } = setup({
      deps: { modal: { spawn: async () => "x", result: async () => { throw new Error("500"); } } },
    });
    expect((await handlePoll(pollReq(signTicket("fc-1", "secret")), deps)).status).toBe(502);
  });
});

describe("handleBudget", () => {
  it("returns today's and this month's usage with a short CDN cache", async () => {
    const { deps } = setup();
    await handleSend(sendReq(hi), deps);
    const res = await handleBudget(deps);
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toContain("s-maxage=10");
    expect(await res.json()).toEqual({ today: { used: 1, cap: 120 }, month: { used: 1, cap: 3000 } });
  });

  it("serves a cached read for 10 seconds", async () => {
    let t = NOW.getTime();
    const { deps } = setup({ deps: { now: () => new Date(t) } });
    await handleSend(sendReq(hi), deps);
    const first = await (await handleBudget(deps)).json();
    await deps.counter.incr(budgetKeys(NOW).day);
    expect((await (await handleBudget(deps)).json()).today.used).toBe(first.today.used);
    t += 11_000;
    expect((await (await handleBudget(deps)).json()).today.used).toBe(first.today.used + 1);
  });

  it("returns 503 when the counter store is down", async () => {
    const down = { ...memoryCounter(), get: async () => { throw new Error("redis down"); } };
    const { deps } = setup({ deps: { counter: down } });
    expect((await handleBudget(deps)).status).toBe(503);
  });
});
