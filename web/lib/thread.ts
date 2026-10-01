import { CONTEXT_TURNS, type Turn } from "./validate";

export type Status = "sending" | "delivered" | "failed" | "received";
export type Message = { id: string; me: boolean; text: string; at: number; status: Status };

export type Row =
  | { type: "time"; key: string; label: string }
  | { type: "bubble"; key: string; message: Message; first: boolean; tail: boolean };

const STATUSES: readonly Status[] = ["sending", "delivered", "failed", "received"];

// iMessage starts a new timestamp block after a quiet stretch.
export const GAP_MS = 15 * 60 * 1000;

export function layout(messages: Message[], label: (at: number) => string): Row[] {
  const rows: Row[] = [];
  messages.forEach((m, i) => {
    const prev = i > 0 ? messages[i - 1] : undefined;
    const next = i < messages.length - 1 ? messages[i + 1] : undefined;
    const newBlock = !prev || m.at - prev.at > GAP_MS;
    if (newBlock) rows.push({ type: "time", key: `t-${m.id}`, label: label(m.at) });
    const first = !prev || newBlock || prev.me !== m.me;
    const tail = !next || next.me !== m.me || next.at - m.at > GAP_MS;
    rows.push({ type: "bubble", key: m.id, message: m, first, tail });
  });
  return rows;
}

// "Delivered" sits under the visitor's latest message only, and only once the
// server accepted it.
export function lastDeliveredId(messages: Message[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].me) return messages[i].status === "delivered" ? messages[i].id : null;
  }
  return null;
}

export function toHistory(messages: Message[]): Turn[] {
  return messages
    .filter((m) => m.status !== "failed")
    .slice(-CONTEXT_TURNS)
    .map((m) => ({ me: m.me, text: m.text }));
}

// Reads back what localStorage held. A message saved as "sending" belonged to a
// page that closed mid-send; it will never resolve, so it comes back retryable.
export function restore(raw: unknown): Message[] {
  if (!Array.isArray(raw)) return [];
  const out: Message[] = [];
  for (const item of raw) {
    if (typeof item !== "object" || item === null) continue;
    const { id, me, text, at, status } = item as Record<string, unknown>;
    if (
      typeof id !== "string" ||
      typeof me !== "boolean" ||
      typeof text !== "string" ||
      typeof at !== "number" ||
      !STATUSES.includes(status as Status)
    ) {
      continue;
    }
    out.push({ id, me, text, at, status: status === "sending" ? "failed" : (status as Status) });
  }
  return out;
}

// Newer ICU puts a narrow no-break space (U+202F) before AM/PM; \s matches it.
const clock = (d: Date) =>
  d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }).replace(/\s/g, " ");

export function formatTimeHeader(at: number, now: number): string {
  const d = new Date(at);
  const today = new Date(now);
  const yesterday = new Date(now);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return `Today ${clock(d)}`;
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${clock(d)}`;
  const day = d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  return `${day} at ${clock(d)}`;
}
