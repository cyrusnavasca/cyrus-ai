import { Redis } from "@upstash/redis";
import { ConfigError } from "./config";
import type { Counter } from "./limits";

// Upstash from the Vercel Marketplace injects UPSTASH_REDIS_REST_*; projects
// migrated from Vercel KV get KV_REST_API_* instead. Accept either.
export function redisCounter(env: Record<string, string | undefined>): Counter {
  const url = env.UPSTASH_REDIS_REST_URL || env.KV_REST_API_URL;
  const token = env.UPSTASH_REDIS_REST_TOKEN || env.KV_REST_API_TOKEN;
  if (!url || !token) throw new ConfigError(["UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"]);
  const redis = new Redis({ url, token });
  return {
    incr: (key) => redis.incr(key),
    decr: (key) => redis.decr(key),
    expire: (key, seconds) => redis.expire(key, seconds),
    get: (key) => redis.get<number | string>(key),
  };
}
