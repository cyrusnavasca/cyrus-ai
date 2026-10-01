import { describe, expect, it, vi } from "vitest";
import { sendTurns } from "./chatClient";

type Step = Response | Error;
const res = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

function fakeFetch(send: Step[], poll: Step[] = []) {
  const urls: string[] = [];
  const f = (async (url: RequestInfo | URL) => {
    const u = String(url);
    urls.push(u);
    const queue = u.startsWith("/api/send") ? send : poll;
    const next = queue.shift();
    if (!next) throw new Error(`nothing queued for ${u}`);
    if (next instanceof Error) throw next;
    return next;
  }) as typeof fetch;
  return { f, urls };
}

const noSleep = async () => {};
const HISTORY = [{ me: true, text: "hi" }];

describe("sendTurns", () => {
  it("polls through pending and returns the trimmed reply", async () => {
    const { f, urls } = fakeFetch(
      [res(200, { ticket: "fc-1.sig/+" })],
      [res(202, { pending: true }), res(202, { pending: true }), res(200, { reply: "  lol  " })],
    );
    const onAccepted = vi.fn();
    expect(await sendTurns(HISTORY, { fetch: f, sleep: noSleep, onAccepted })).toEqual({ kind: "reply", text: "lol" });
    expect(onAccepted).toHaveBeenCalledTimes(1);
    expect(urls[1]).toBe("/api/poll?ticket=fc-1.sig%2F%2B");
  });

  it.each([
    [{ reason: "minute" }, { kind: "limit", reason: "minute" }],
    [{ reason: "day" }, { kind: "limit", reason: "day" }],
    [{ reason: "budget" }, { kind: "budget" }],
    [{ reason: "???" }, { kind: "failed" }],
  ])("maps 429 %j", async (body, want) => {
    const { f } = fakeFetch([res(429, body)]);
    expect(await sendTurns(HISTORY, { fetch: f, sleep: noSleep })).toEqual(want);
  });

  it("fails without accepting on a server error or a dead network", async () => {
    for (const step of [res(500, {}), res(403, {}), new TypeError("Failed to fetch")]) {
      const onAccepted = vi.fn();
      const { f } = fakeFetch([step]);
      expect(await sendTurns(HISTORY, { fetch: f, sleep: noSleep, onAccepted })).toEqual({ kind: "failed" });
      expect(onAccepted).not.toHaveBeenCalled();
    }
  });

  it("keeps polling through a dropped connection", async () => {
    const { f } = fakeFetch(
      [res(200, { ticket: "t.s" })],
      [new TypeError("Failed to fetch"), res(200, { reply: "back" })],
    );
    expect(await sendTurns(HISTORY, { fetch: f, sleep: noSleep })).toEqual({ kind: "reply", text: "back" });
  });

  it("fails on a poll error status or an empty reply", async () => {
    for (const step of [res(502, {}), res(200, { reply: "   " }), res(200, {})]) {
      const { f } = fakeFetch([res(200, { ticket: "t.s" })], [step]);
      expect(await sendTurns(HISTORY, { fetch: f, sleep: noSleep })).toEqual({ kind: "failed" });
    }
  });

  it("gives up after maxPolls", async () => {
    const { f } = fakeFetch([res(200, { ticket: "t.s" })], [res(202, {}), res(202, {}), res(202, {})]);
    expect(await sendTurns(HISTORY, { fetch: f, sleep: noSleep, maxPolls: 3 })).toEqual({ kind: "failed" });
  });
});
