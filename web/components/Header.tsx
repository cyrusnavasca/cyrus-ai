"use client";

import { batteryLevel, pillText } from "../lib/budget";
import { COPY } from "../lib/copy";
import type { Budget } from "../lib/limits";
import styles from "./chat.module.css";

export function Header({ budget, onClear }: { budget: Budget | null; onClear: () => void }) {
  const level = batteryLevel(budget);
  return (
    <header className={styles.header}>
      <span className={styles.back} aria-hidden>
        <svg width="12" height="20" viewBox="0 0 12 20">
          <path d="M10 2 2 10l8 8" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <button
        type="button"
        className={styles.contact}
        onClick={() => {
          if (window.confirm(COPY.clearConfirm)) onClear();
        }}
      >
        {/* Replace web/public/avatar.png to change the picture; nothing else needs editing. */}
        <img className={styles.avatar} src="/avatar.png" alt="" width={50} height={50} />
        <span className={styles.name}>
          {COPY.name} <span className={styles.chev}>›</span>
        </span>
      </button>
      {level !== null && <span className={styles.pill}>{pillText(level)}</span>}
    </header>
  );
}
