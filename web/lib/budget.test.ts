import { describe, expect, it } from "vitest";
import { batteryColor, batteryLevel, budgetSummary, isExhausted, monthLine, pillText } from "./budget";

const b = (today: number, month = 0) => ({ today: { used: today, cap: 120 }, month: { used: month, cap: 3000 } });

describe("budget display", () => {
  it("level is the share of today left, clamped", () => {
    expect(batteryLevel(null)).toBeNull();
    expect(batteryLevel(b(30))).toBe(0.75);
    expect(batteryLevel(b(500))).toBe(0);
  });

  it("colour follows iOS thresholds", () => {
    expect(batteryColor(0.25)).toBe("green");
    expect(batteryColor(0.2)).toBe("yellow");
    expect(batteryColor(0.1)).toBe("yellow");
    expect(batteryColor(0.05)).toBe("red");
  });

  it("is exhausted when today or the month is used up", () => {
    expect(isExhausted(b(119))).toBe(false);
    expect(isExhausted(b(120))).toBe(true);
    expect(isExhausted(b(0, 3000))).toBe(true);
  });

  it("formats the popover, month line and pill", () => {
    expect(budgetSummary(b(87))).toBe("87 / 120 texts used today · resets at midnight UTC · shared by everyone");
    expect(monthLine(b(0, 1500))).toBeNull();
    expect(monthLine(b(0, 1600))).toBe("1600 / 3000 texts used this month");
    expect(pillText(0.2667)).toBe("🔋 27% left today");
  });
});
