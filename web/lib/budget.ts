import type { Budget } from "./limits";

export type BatteryColor = "green" | "yellow" | "red";

export function batteryLevel(b: Budget | null): number | null {
  if (!b) return null;
  return Math.min(1, Math.max(0, (b.today.cap - b.today.used) / b.today.cap));
}

// iOS: green above 20%, yellow from 20% down to 10%, red below 10%.
export function batteryColor(level: number): BatteryColor {
  if (level > 0.2) return "green";
  return level >= 0.1 ? "yellow" : "red";
}

export function isExhausted(b: Budget): boolean {
  return b.today.used >= b.today.cap || b.month.used >= b.month.cap;
}

export function budgetSummary(b: Budget): string {
  return `${b.today.used} / ${b.today.cap} texts used today · resets at midnight UTC · shared by everyone`;
}

export function monthLine(b: Budget): string | null {
  return b.month.used / b.month.cap > 0.5 ? `${b.month.used} / ${b.month.cap} texts used this month` : null;
}

export function pillText(level: number): string {
  return `🔋 ${Math.round(level * 100)}% left today`;
}
