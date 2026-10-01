"use client";

import { useState } from "react";
import { batteryColor, batteryLevel, budgetSummary, monthLine } from "../lib/budget";
import type { Budget } from "../lib/limits";
import styles from "./chat.module.css";

const FILL = { green: "var(--battery-green)", yellow: "var(--battery-yellow)", red: "var(--battery-red)" };

// The status-bar battery is the shared GPU budget for today.
export function Battery({ budget }: { budget: Budget | null }) {
  const [open, setOpen] = useState(false);
  const level = batteryLevel(budget);
  const shown = level ?? 1;
  const summary = budget ? budgetSummary(budget) : "Loading budget";
  const month = budget ? monthLine(budget) : null;

  return (
    <span className={styles.batteryWrap}>
      <button
        type="button"
        className={styles.batteryButton}
        onClick={() => setOpen((o) => !o)}
        aria-label={summary}
        aria-expanded={open}
        title={summary}
      >
        <svg width="27" height="13" viewBox="0 0 27 13" aria-hidden>
          <rect x="0.5" y="0.5" width="23" height="12" rx="3.5" fill="none" stroke="currentColor" opacity="0.4" />
          <rect
            x="2"
            y="2"
            width={Math.max(1.5, 20 * shown)}
            height="9"
            rx="2"
            fill={level === null ? "currentColor" : FILL[batteryColor(shown)]}
          />
          <path d="M25 4.5v4a2 2 0 0 0 0-4z" fill="currentColor" opacity="0.4" />
        </svg>
      </button>
      {open && budget && (
        <div className={styles.popover} role="status">
          <div>{summary}</div>
          {month && <div>{month}</div>}
        </div>
      )}
    </span>
  );
}
