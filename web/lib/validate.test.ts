import { describe, expect, it } from "vitest";
import { MAX_CHARS, validateHistory } from "./validate";

const turn = (me: boolean, text: string) => ({ me, text });

describe("validateHistory", () => {
  it("accepts a single visitor message and trims it", () => {
    expect(validateHistory({ history: [turn(true, "  hey ")] })).toEqual({
      ok: true,
      history: [turn(true, "hey")],
    });
  });

  it("keeps only the last six turns", () => {
    const history = Array.from({ length: 10 }, (_, i) => turn(i % 2 === 1, `m${i}`));
    const r = validateHistory({ history });
    expect(r.ok && r.history.map((t) => t.text)).toEqual(["m4", "m5", "m6", "m7", "m8", "m9"]);
  });

  it("clamps older turns to MAX_CHARS without splitting an emoji", () => {
    const older = "a" + "😭".repeat(200); // 401 UTF-16 units
    const r = validateHistory({ history: [turn(false, older), turn(true, "ok")] });
    if (!r.ok) throw new Error(r.error);
    expect(r.history[0].text.length).toBe(MAX_CHARS - 1);
    expect(r.history[0].text.endsWith("😭")).toBe(true);
  });

  it("accepts a newest message of exactly MAX_CHARS, counted as the browser counts", () => {
    expect(validateHistory({ history: [turn(true, "😭".repeat(150))] }).ok).toBe(true);
    expect(validateHistory({ history: [turn(true, "😭".repeat(151))] }).ok).toBe(false);
    expect(validateHistory({ history: [turn(true, "x".repeat(301))] }).ok).toBe(false);
  });

  it("rejects when the last turn is not the visitor's", () => {
    expect(validateHistory({ history: [turn(true, "hi"), turn(false, "yo")] }).ok).toBe(false);
  });

  it("rejects an empty newest message", () => {
    expect(validateHistory({ history: [turn(true, "   ")] }).ok).toBe(false);
  });

  it("drops empty older turns", () => {
    expect(validateHistory({ history: [turn(false, "  "), turn(true, "hi")] })).toEqual({
      ok: true,
      history: [turn(true, "hi")],
    });
  });

  it.each([
    null,
    "str",
    5,
    {},
    { history: [] },
    { history: "hi" },
    { history: [5] },
    { history: [{ me: "yes", text: "hi" }] },
    { history: [{ me: true, text: 5 }] },
  ])("rejects malformed body %j", (body) => {
    expect(validateHistory(body).ok).toBe(false);
  });
});
