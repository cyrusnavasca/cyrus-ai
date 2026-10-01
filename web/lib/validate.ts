// The request shape /api/send accepts. Mirrors src/style_ft/serving/guard.py on
// the Modal side, which re-checks everything: the proxy is the first gate, not
// the only one.

export type Turn = { me: boolean; text: string };

// String length here is UTF-16 units, the same unit a textarea's maxLength
// counts, so the browser and the server agree on what "300" means. Python
// counts code points, which is never more, so Modal never rejects what this
// accepted.
export const MAX_CHARS = 300;
// The pairs were built with a six-turn window; more context degrades replies.
export const CONTEXT_TURNS = 6;

export type Validated = { ok: true; history: Turn[] } | { ok: false; error: string };

const fail = (error: string): Validated => ({ ok: false, error });

function clamp(text: string): string {
  if (text.length <= MAX_CHARS) return text;
  // Never cut between the two halves of an emoji.
  const end = /[\uD800-\uDBFF]/.test(text[MAX_CHARS - 1]) ? MAX_CHARS - 1 : MAX_CHARS;
  return text.slice(0, end);
}

export function validateHistory(body: unknown): Validated {
  if (typeof body !== "object" || body === null) return fail("body must be an object");
  const raw = (body as { history?: unknown }).history;
  if (!Array.isArray(raw) || raw.length === 0) return fail("history must be a non-empty array");

  const turns: Turn[] = [];
  for (const item of raw.slice(-CONTEXT_TURNS)) {
    if (typeof item !== "object" || item === null) return fail("each turn must be an object");
    const { me, text } = item as { me?: unknown; text?: unknown };
    if (typeof me !== "boolean" || typeof text !== "string") {
      return fail("each turn needs me:boolean and text:string");
    }
    turns.push({ me, text: text.trim() });
  }

  const newest = turns[turns.length - 1];
  if (!newest.me) return fail("the last turn must be the visitor's");
  if (newest.text.length === 0) return fail("message is empty");
  if (newest.text.length > MAX_CHARS) return fail(`message is over ${MAX_CHARS} characters`);

  return {
    ok: true,
    history: turns.filter((t) => t.text.length > 0).map((t) => ({ me: t.me, text: clamp(t.text) })),
  };
}
