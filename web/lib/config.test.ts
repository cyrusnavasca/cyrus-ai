import { describe, expect, it } from "vitest";
import { ConfigError, loadConfig } from "./config";

const BASE = {
  MODAL_BASE_URL: "https://ws--style-ft-ui-web.modal.run/",
  STYLE_FT_TOKEN: "t",
  STYLE_FT_RUN: "chat-v3",
  TICKET_SECRET: "s",
};

describe("loadConfig", () => {
  it("reads the required values and strips a trailing slash", () => {
    const c = loadConfig(BASE);
    expect(c.modalBaseUrl).toBe("https://ws--style-ft-ui-web.modal.run");
    expect([c.token, c.run, c.ticketSecret]).toEqual(["t", "chat-v3", "s"]);
  });

  it("uses the default limits", () => {
    expect(loadConfig(BASE).limits).toEqual({
      perIpMinute: 5,
      perIpDay: 20,
      dailyCap: 120,
      monthlyCap: 3000,
    });
  });

  it("reads limit overrides", () => {
    const c = loadConfig({ ...BASE, DAILY_MESSAGE_CAP: "10", PER_IP_PER_MINUTE: "1" });
    expect(c.limits.dailyCap).toBe(10);
    expect(c.limits.perIpMinute).toBe(1);
  });

  it("ignores zero, negative or junk limits", () => {
    const c = loadConfig({ ...BASE, PER_IP_PER_DAY: "0", DAILY_MESSAGE_CAP: "lots", MONTHLY_MESSAGE_CAP: "-1" });
    expect(c.limits).toEqual({ perIpMinute: 5, perIpDay: 20, dailyCap: 120, monthlyCap: 3000 });
  });

  it("names every missing required variable", () => {
    try {
      loadConfig({});
      throw new Error("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(ConfigError);
      expect((e as ConfigError).missing).toEqual([
        "MODAL_BASE_URL",
        "STYLE_FT_TOKEN",
        "STYLE_FT_RUN",
        "TICKET_SECRET",
      ]);
    }
  });

  it("treats an empty string as missing", () => {
    expect(() => loadConfig({ ...BASE, STYLE_FT_TOKEN: "" })).toThrow(ConfigError);
  });
});
