import { describe, expect, it } from "vitest";
import { signTicket, verifyTicket } from "./ticket";

const SECRET = "test-secret";

describe("tickets", () => {
  it("round-trips a call id", () => {
    expect(verifyTicket(signTicket("fc-01ABC", SECRET), SECRET)).toBe("fc-01ABC");
  });

  it("rejects a ticket signed with another secret", () => {
    expect(verifyTicket(signTicket("fc-01ABC", "other"), SECRET)).toBeNull();
  });

  it("rejects a tampered id", () => {
    const t = signTicket("fc-01ABC", SECRET).replace("fc-01ABC", "fc-01ABD");
    expect(verifyTicket(t, SECRET)).toBeNull();
  });

  it("rejects a tampered signature", () => {
    const t = signTicket("fc-01ABC", SECRET);
    const last = t.at(-1) === "A" ? "B" : "A";
    expect(verifyTicket(t.slice(0, -1) + last, SECRET)).toBeNull();
  });

  it.each(["", ".", "fc-1", ".sig", "fc 1.sig", "../x.sig"])("rejects malformed %j", (t) => {
    expect(verifyTicket(t, SECRET)).toBeNull();
  });
});
