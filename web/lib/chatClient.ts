import type { Turn } from "./validate";

export type Outcome =
  | { kind: "reply"; text: string }
  | { kind: "limit"; reason: "minute" | "day" }
  | { kind: "budget" }
  | { kind: "failed" };

type SendDeps = {
  fetch: typeof fetch;
  sleep: (ms: number) => Promise<void>;
  onAccepted?: () => void;
  pollMs?: number;
  maxPolls?: number;
};

const FAILED: Outcome = { kind: "failed" };
const body = async (r: Response): Promise<Record<string, unknown>> =>
  ((await r.json().catch(() => ({}))) ?? {}) as Record<string, unknown>;

// One message: send, then poll until the model answers. 300 polls at 1.5s is
// about 7.5 minutes, longer than any cold start.
export async function sendTurns(history: Turn[], d: SendDeps): Promise<Outcome> {
  let sent: Response;
  try {
    sent = await d.fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history }),
    });
  } catch {
    return FAILED;
  }
  if (sent.status === 429) {
    const { reason } = await body(sent);
    if (reason === "budget") return { kind: "budget" };
    if (reason === "minute" || reason === "day") return { kind: "limit", reason };
    return FAILED;
  }
  if (!sent.ok) return FAILED;
  const { ticket } = await body(sent);
  if (typeof ticket !== "string") return FAILED;
  d.onAccepted?.();

  for (let i = 0; i < (d.maxPolls ?? 300); i++) {
    await d.sleep(d.pollMs ?? 1500);
    let p: Response;
    try {
      p = await d.fetch(`/api/poll?ticket=${encodeURIComponent(ticket)}`);
    } catch {
      continue; // phones drop connections; the reply is still coming
    }
    if (p.status === 202) continue;
    if (!p.ok) return FAILED;
    const { reply } = await body(p);
    return typeof reply === "string" && reply.trim() ? { kind: "reply", text: reply.trim() } : FAILED;
  }
  return FAILED;
}
