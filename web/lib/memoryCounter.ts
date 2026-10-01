import type { Counter } from "./limits";

// A Counter in a Map: the fake for tests and the store for MOCK_MODAL=1 local
// runs. Expiries are recorded so tests can assert them, not enforced.
export function memoryCounter(): Counter & { ttl: Map<string, number> } {
  const values = new Map<string, number>();
  const ttl = new Map<string, number>();
  const add = (key: string, by: number) => {
    const n = (values.get(key) ?? 0) + by;
    values.set(key, n);
    return n;
  };
  return {
    ttl,
    async incr(key) {
      return add(key, 1);
    },
    async decr(key) {
      return add(key, -1);
    },
    async expire(key, seconds) {
      ttl.set(key, seconds);
      return 1;
    },
    async get(key) {
      return values.has(key) ? (values.get(key) as number) : null;
    },
  };
}
