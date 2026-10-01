import { checkBotId } from "botid/server";
import { ConfigError, loadConfig } from "./config";
import type { Deps } from "./handlers";
import { memoryCounter } from "./memoryCounter";
import { mockModal } from "./mockModal";
import { modalClient } from "./modal";
import { redisCounter } from "./redis";

// Built once per server process. Kept on globalThis because Next can load a
// module once per route bundle; in mock mode the three routes must share one
// in-memory counter or /api/budget never sees what /api/send charged.
const g = globalThis as { __cyrusgptDeps?: Deps };

export function getDeps(env: Record<string, string | undefined> = process.env):
  | { ok: true; deps: Deps }
  | { ok: false } {
  if (g.__cyrusgptDeps) return { ok: true, deps: g.__cyrusgptDeps };
  // Never in production: a deploy with MOCK_MODAL left on must not silently
  // serve a fake model with no budget.
  const mock = env.MOCK_MODAL === "1" && env.NODE_ENV !== "production";
  try {
    const config = loadConfig(
      mock
        ? { MODAL_BASE_URL: "http://mock.invalid", STYLE_FT_TOKEN: "mock", STYLE_FT_RUN: "mock", TICKET_SECRET: "mock", ...env }
        : env,
    );
    g.__cyrusgptDeps = {
      config,
      counter: mock ? memoryCounter() : redisCounter(env),
      modal: mock ? mockModal() : modalClient(config),
      isBot: mock
        ? async () => false
        : async () => {
            const r = await checkBotId();
            return "isBot" in r && r.isBot === true;
          },
      now: () => new Date(),
      log: (event, fields = {}) => console.log(JSON.stringify({ event, ...fields })),
    };
    return { ok: true, deps: g.__cyrusgptDeps };
  } catch (e) {
    console.error(JSON.stringify({ event: "config.error", error: e instanceof ConfigError ? e.message : String(e) }));
    return { ok: false };
  }
}
