import { describe, expect, it } from "vitest";
import { budgetKeys, chargeBudget, checkIp, type Limits, readBudget, refundBudget } from "./limits";
import { memoryCounter } from "./memoryCounter";

const LIMITS: Limits = { perIpMinute: 2, perIpDay: 3, dailyCap: 4, monthlyCap: 6 };
const at = (iso: string) => new Date(iso);
const NOON = at("2026-10-01T12:00:10Z");

describe("checkIp", () => {
  it("allows up to the per-minute limit, then refuses", async () => {
    const c = memoryCounter();
    expect(await checkIp(c, "1.1.1.1", LIMITS, NOON)).toBe("ok");
    expect(await checkIp(c, "1.1.1.1", LIMITS, NOON)).toBe("ok");
    expect(await checkIp(c, "1.1.1.1", LIMITS, NOON)).toBe("minute");
  });

  it("starts a fresh minute window", async () => {
    const c = memoryCounter();
    await checkIp(c, "1.1.1.1", LIMITS, NOON);
    await checkIp(c, "1.1.1.1", LIMITS, NOON);
    expect(await checkIp(c, "1.1.1.1", LIMITS, at("2026-10-01T12:01:05Z"))).toBe("ok");
  });

  it("enforces the per-day limit across minutes", async () => {
    const c = memoryCounter();
    for (const m of ["00", "01", "02"]) {
      expect(await checkIp(c, "1.1.1.1", LIMITS, at(`2026-10-01T12:${m}:00Z`))).toBe("ok");
    }
    expect(await checkIp(c, "1.1.1.1", LIMITS, at("2026-10-01T12:03:00Z"))).toBe("day");
  });

  it("counts each IP separately", async () => {
    const c = memoryCounter();
    await checkIp(c, "1.1.1.1", LIMITS, NOON);
    await checkIp(c, "1.1.1.1", LIMITS, NOON);
    expect(await checkIp(c, "2.2.2.2", LIMITS, NOON)).toBe("ok");
  });

  it("sets an expiry on every key it creates", async () => {
    const c = memoryCounter();
    await checkIp(c, "1.1.1.1", LIMITS, NOON);
    expect([...c.ttl.values()].sort()).toEqual([120, 172800]);
  });
});

describe("chargeBudget", () => {
  it("charges up to the daily cap, then refuses without leaving the counter raised", async () => {
    const c = memoryCounter();
    for (let i = 0; i < 4; i++) expect(await chargeBudget(c, LIMITS, NOON)).toBe(true);
    expect(await chargeBudget(c, LIMITS, NOON)).toBe(false);
    expect((await readBudget(c, LIMITS, NOON)).today.used).toBe(4);
    expect(await c.get(budgetKeys(NOON).day)).toBe(4);
  });

  it("applies the monthly cap across days", async () => {
    const c = memoryCounter();
    const day1 = at("2026-10-01T12:00:00Z");
    const day2 = at("2026-10-02T12:00:00Z");
    for (let i = 0; i < 4; i++) await chargeBudget(c, LIMITS, day1);
    expect(await chargeBudget(c, LIMITS, day2)).toBe(true);
    expect(await chargeBudget(c, LIMITS, day2)).toBe(true);
    expect(await chargeBudget(c, LIMITS, day2)).toBe(false);
    expect((await readBudget(c, LIMITS, day2)).month.used).toBe(6);
  });

  it("refundBudget gives the message back", async () => {
    const c = memoryCounter();
    await chargeBudget(c, LIMITS, NOON);
    await refundBudget(c, NOON);
    expect(await readBudget(c, LIMITS, NOON)).toEqual({
      today: { used: 0, cap: 4 },
      month: { used: 0, cap: 6 },
    });
  });

  it("keys expire after two days and 32 days", async () => {
    const c = memoryCounter();
    await chargeBudget(c, LIMITS, NOON);
    const k = budgetKeys(NOON);
    expect(k).toEqual({ day: "budget:day:2026-10-01", month: "budget:month:2026-10" });
    expect(c.ttl.get(k.day)).toBe(2 * 86400);
    expect(c.ttl.get(k.month)).toBe(32 * 86400);
  });
});

describe("readBudget", () => {
  it("reads zero for an unused day", async () => {
    expect((await readBudget(memoryCounter(), LIMITS, NOON)).today).toEqual({ used: 0, cap: 4 });
  });

  it("never reports outside 0..cap, even if concurrent requests overshoot", async () => {
    const c = memoryCounter();
    for (let i = 0; i < 10; i++) await c.incr(budgetKeys(NOON).day);
    await c.decr(budgetKeys(NOON).month);
    const b = await readBudget(c, LIMITS, NOON);
    expect(b.today.used).toBe(4);
    expect(b.month.used).toBe(0);
  });
});
