import type { Config } from "./config";
import type { Turn } from "./validate";

// The only file that sends the Modal token. It talks to deploy/serve.py's two
// endpoints, which keep the spawn-then-poll split: Modal caps a web request at
// 150s and a cold 8B load can take longer, so nothing here waits on the GPU.

export type PollResult = { pending: true } | { pending: false; reply: string };

export interface ModalClient {
  spawn(history: Turn[]): Promise<string>;
  result(callId: string): Promise<PollResult>;
}

export class ModalError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ModalError";
  }
}

export function modalClient(
  cfg: Pick<Config, "modalBaseUrl" | "token" | "run">,
  fetchImpl: typeof fetch = fetch,
): ModalClient {
  const auth = { Authorization: `Bearer ${cfg.token}` };
  return {
    async spawn(history) {
      const res = await fetchImpl(`${cfg.modalBaseUrl}/generate`, {
        method: "POST",
        headers: { ...auth, "Content-Type": "application/json" },
        body: JSON.stringify({ history, run: cfg.run }),
        // The CPU web container can itself be cold; give it room, but not forever.
        signal: AbortSignal.timeout(25_000),
      });
      if (!res.ok) throw new ModalError(`generate returned ${res.status}`, res.status);
      const data = (await res.json().catch(() => ({}))) as { id?: unknown };
      if (typeof data.id !== "string") throw new ModalError("generate returned no id");
      return data.id;
    },
    async result(callId) {
      const res = await fetchImpl(`${cfg.modalBaseUrl}/result?id=${encodeURIComponent(callId)}`, {
        headers: auth,
        signal: AbortSignal.timeout(10_000),
      });
      if (res.status === 202) return { pending: true };
      if (!res.ok) throw new ModalError(`result returned ${res.status}`, res.status);
      const data = (await res.json().catch(() => ({}))) as { reply?: unknown };
      if (typeof data.reply !== "string") throw new ModalError("result returned no reply");
      return { pending: false, reply: data.reply };
    },
  };
}
