// Usage limits as plain counters. Every key has the window in its name and an
// expiry, so a window "resets" by being a different key, and nothing lives in
// Redis longer than it can matter.
//
// The budget is counted in messages, not dollars: the counters are ours, update
// instantly, and do not depend on Modal's billing API, which reports spend after
// the fact and has no remaining-credit endpoint. See the spec, section 6, for
// how the caps were sized.

export interface Counter {
  incr(key: string): Promise<number>;
  decr(key: string): Promise<number>;
  expire(key: string, seconds: number): Promise<unknown>;
  get(key: string): Promise<number | string | null>;
}

export type Limits = { perIpMinute: number; perIpDay: number; dailyCap: number; monthlyCap: number };

export type Budget = {
  today: { used: number; cap: number };
  month: { used: number; cap: number };
};

const DAY_S = 86_400;

export function budgetKeys(now: Date): { day: string; month: string } {
  const iso = now.toISOString();
  return { day: `budget:day:${iso.slice(0, 10)}`, month: `budget:month:${iso.slice(0, 7)}` };
}

async function bump(c: Counter, key: string, ttlSeconds: number): Promise<number> {
  const n = await c.incr(key);
  if (n === 1) await c.expire(key, ttlSeconds);
  return n;
}

export async function checkIp(
  c: Counter,
  ip: string,
  limits: Limits,
  now: Date,
): Promise<"ok" | "minute" | "day"> {
  const minute = Math.floor(now.getTime() / 60_000);
  if ((await bump(c, `ip:min:${ip}:${minute}`, 120)) > limits.perIpMinute) return "minute";
  const day = now.toISOString().slice(0, 10);
  if ((await bump(c, `ip:day:${ip}:${day}`, 2 * DAY_S)) > limits.perIpDay) return "day";
  return "ok";
}

// Charged at spawn, not at completion: a reply that later fails still used GPU
// time. `now` must be the same instant for the charge and any refund, or a
// failure that straddles midnight UTC refunds the wrong day.
export async function chargeBudget(c: Counter, limits: Limits, now: Date): Promise<boolean> {
  const k = budgetKeys(now);
  const day = await bump(c, k.day, 2 * DAY_S);
  const month = await bump(c, k.month, 32 * DAY_S);
  if (day > limits.dailyCap || month > limits.monthlyCap) {
    await refundBudget(c, now);
    return false;
  }
  return true;
}

export async function refundBudget(c: Counter, now: Date): Promise<void> {
  const k = budgetKeys(now);
  await c.decr(k.day);
  await c.decr(k.month);
}

function toCount(v: number | string | null, cap: number): number {
  const n = typeof v === "number" ? v : Number(v ?? 0);
  // Concurrent requests can push a counter past its cap for an instant before
  // they refund; never show a visitor 121 / 120.
  return Number.isFinite(n) ? Math.min(cap, Math.max(0, n)) : 0;
}

export async function readBudget(c: Counter, limits: Limits, now: Date): Promise<Budget> {
  const k = budgetKeys(now);
  const [day, month] = await Promise.all([c.get(k.day), c.get(k.month)]);
  return {
    today: { used: toCount(day, limits.dailyCap), cap: limits.dailyCap },
    month: { used: toCount(month, limits.monthlyCap), cap: limits.monthlyCap },
  };
}
