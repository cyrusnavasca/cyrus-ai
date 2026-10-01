"use client";

import { useEffect, useState } from "react";
import type { Budget } from "../lib/limits";
import { Battery } from "./Battery";
import styles from "./chat.module.css";

// Only drawn inside the desktop frame; a real phone draws its own status bar.
export function StatusBar({ budget }: { budget: Budget | null }) {
  // Empty until mounted, so the server render and the first client render agree.
  const [time, setTime] = useState("");
  useEffect(() => {
    const tick = () =>
      setTime(
        new Date()
          .toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })
          .replace(/[\s ]*[AP]M$/i, ""),
      );
    tick();
    const t = setInterval(tick, 10_000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className={styles.statusBar}>
      <span>{time}</span>
      <span className={styles.glyphs}>
        <svg width="18" height="12" viewBox="0 0 18 12" fill="currentColor" aria-hidden>
          <rect x="0" y="8" width="3" height="4" rx="1" />
          <rect x="5" y="5.5" width="3" height="6.5" rx="1" />
          <rect x="10" y="3" width="3" height="9" rx="1" />
          <rect x="15" y="0" width="3" height="12" rx="1" />
        </svg>
        <svg width="16" height="12" viewBox="0 0 16 12" fill="currentColor" aria-hidden>
          <path d="M8 11.5 5.6 9a3.4 3.4 0 0 1 4.8 0z" />
          <path d="M3.3 6.7a6.6 6.6 0 0 1 9.4 0l-1.2 1.2a4.9 4.9 0 0 0-7 0z" />
          <path d="M1 4.4a9.9 9.9 0 0 1 14 0l-1.2 1.2a8.2 8.2 0 0 0-11.6 0z" />
        </svg>
        <Battery budget={budget} />
      </span>
    </div>
  );
}
