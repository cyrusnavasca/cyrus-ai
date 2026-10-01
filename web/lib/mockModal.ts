import type { ModalClient } from "./modal";

// MOCK_MODAL=1: a fake model for working on the page with no GPU and no bill.
// The first reply takes 14s so the "waking up" caption can be seen; later ones 2s.
const COLD_MS = 14_000;
const WARM_MS = 2_000;

export function mockModal(): ModalClient {
  const calls = new Map<string, { readyAt: number; reply: string }>();
  let n = 0;
  return {
    async spawn(history) {
      n += 1;
      const last = history[history.length - 1]?.text ?? "";
      const id = `mock-${n}`;
      calls.set(id, {
        readyAt: Date.now() + (n === 1 ? COLD_MS : WARM_MS),
        reply: `lol "${last.slice(0, 40)}" 😭`,
      });
      return id;
    },
    async result(id) {
      const call = calls.get(id);
      if (!call) throw new Error("unknown mock call");
      return Date.now() < call.readyAt ? { pending: true } : { pending: false, reply: call.reply };
    },
  };
}
