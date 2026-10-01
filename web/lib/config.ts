import type { Limits } from "./limits";

// The only place env vars are read. No defaults for anything secret: a missing
// token fails the request with a 503, it never falls back to a value someone
// could read in git (the same rule deploy/serve.py follows).

export type Config = {
  modalBaseUrl: string;
  token: string;
  run: string;
  ticketSecret: string;
  limits: Limits;
};

const REQUIRED = ["MODAL_BASE_URL", "STYLE_FT_TOKEN", "STYLE_FT_RUN", "TICKET_SECRET"] as const;

export class ConfigError extends Error {
  constructor(public missing: string[]) {
    super(`missing env: ${missing.join(", ")}`);
    this.name = "ConfigError";
  }
}

function positiveInt(value: string | undefined, fallback: number): number {
  const n = Number.parseInt(value ?? "", 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

export function loadConfig(env: Record<string, string | undefined> = process.env): Config {
  const missing = REQUIRED.filter((k) => !env[k]);
  if (missing.length > 0) throw new ConfigError([...missing]);
  return {
    modalBaseUrl: (env.MODAL_BASE_URL as string).replace(/\/+$/, ""),
    token: env.STYLE_FT_TOKEN as string,
    run: env.STYLE_FT_RUN as string,
    ticketSecret: env.TICKET_SECRET as string,
    limits: {
      perIpMinute: positiveInt(env.PER_IP_PER_MINUTE, 5),
      perIpDay: positiveInt(env.PER_IP_PER_DAY, 20),
      dailyCap: positiveInt(env.DAILY_MESSAGE_CAP, 120),
      monthlyCap: positiveInt(env.MONTHLY_MESSAGE_CAP, 3000),
    },
  };
}
