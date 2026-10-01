import { describe, expect, it } from "vitest";
import { formatTimeHeader, layout, lastDeliveredId, type Message, restore, toHistory } from "./thread";

const T0 = Date.UTC(2026, 9, 1, 21, 41); // Thu Oct 1 2026, 9:41 PM UTC (tests run with TZ=UTC)
const MIN = 60_000;
const msg = (id: string, me: boolean, at: number, status: Message["status"] = me ? "delivered" : "received"): Message =>
  ({ id, me, text: id, at, status });

describe("layout", () => {
  it("puts a time header first and groups a run with one tail", () => {
    const rows = layout([msg("a", true, T0), msg("b", true, T0 + MIN), msg("c", false, T0 + 2 * MIN)], () => "T");
    expect(rows.map((r) => (r.type === "time" ? "time" : `${r.key}:${r.first ? "F" : ""}${r.tail ? "T" : ""}`)))
      .toEqual(["time", "a:F", "b:T", "c:FT"]);
  });

  it("starts a new header and a new run after a 15-minute gap", () => {
    const rows = layout([msg("a", true, T0), msg("b", true, T0 + 16 * MIN)], () => "T");
    expect(rows.map((r) => (r.type === "time" ? "time" : `${r.key}:${r.first ? "F" : ""}${r.tail ? "T" : ""}`)))
      .toEqual(["time", "a:FT", "time", "b:FT"]);
  });
});

describe("lastDeliveredId", () => {
  it("is the latest visitor message when it was delivered", () => {
    expect(lastDeliveredId([msg("a", true, T0), msg("b", false, T0 + 1), msg("c", true, T0 + 2)])).toBe("c");
    expect(lastDeliveredId([msg("a", true, T0), msg("b", false, T0 + 1)])).toBe("a");
  });

  it("is null when the latest visitor message failed or is still sending", () => {
    expect(lastDeliveredId([msg("a", true, T0), msg("b", true, T0 + 1, "failed")])).toBeNull();
    expect(lastDeliveredId([msg("a", true, T0, "sending")])).toBeNull();
    expect(lastDeliveredId([])).toBeNull();
  });
});

describe("toHistory", () => {
  it("drops failed messages, keeps the one being sent, and caps at six turns", () => {
    const ms = [
      ...Array.from({ length: 7 }, (_, i) => msg(`m${i}`, i % 2 === 0, T0 + i)),
      msg("bad", true, T0 + 8, "failed"),
      msg("now", true, T0 + 9, "sending"),
    ];
    expect(toHistory(ms).map((t) => t.text)).toEqual(["m2", "m3", "m4", "m5", "m6", "now"]);
    expect(toHistory(ms)[0]).toEqual({ me: true, text: "m2" });
  });
});

describe("restore", () => {
  it("turns a message saved mid-send into a failed one", () => {
    expect(restore([msg("a", true, T0, "sending")])[0].status).toBe("failed");
  });

  it("skips malformed entries and survives junk", () => {
    expect(restore([msg("a", true, T0), { id: 1 }, null, "x", { ...msg("b", true, T0), status: "weird" }]))
      .toEqual([msg("a", true, T0)]);
    expect(restore("nope")).toEqual([]);
    expect(restore(null)).toEqual([]);
  });
});

describe("formatTimeHeader", () => {
  it("labels today, yesterday, and older days like iMessage", () => {
    expect(formatTimeHeader(T0, T0 + MIN)).toBe("Today 9:41 PM");
    expect(formatTimeHeader(T0 - 24 * 60 * MIN, T0)).toBe("Yesterday 9:41 PM");
    expect(formatTimeHeader(T0 - 3 * 24 * 60 * MIN, T0)).toBe("Mon, Sep 28 at 9:41 PM");
  });

  it("never emits a narrow no-break space", () => {
    expect(formatTimeHeader(T0, T0 + MIN).includes(String.fromCharCode(0x202f))).toBe(false);
  });
});
