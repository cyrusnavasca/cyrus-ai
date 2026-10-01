"use client";

import { useEffect, useRef, useState } from "react";
import { LINKS } from "../lib/links";
import styles from "./chat.module.css";

// The "+" beside the input: opens a list of links, like iMessage's app menu.
export function PlusMenu() {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !root.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  return (
    <div className={styles.plusWrap} ref={root}>
      <button
        type="button"
        className={`${styles.plus} ${open ? styles.plusOpen : ""}`}
        onClick={() => setOpen((o) => !o)}
        aria-label="Links"
        aria-expanded={open}
      >
        +
      </button>
      {open && (
        <div className={styles.plusMenu} role="menu">
          {LINKS.map((link) => (
            <a
              key={link.label}
              role="menuitem"
              className={styles.plusItem}
              href={link.href || undefined}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setOpen(false)}
            >
              <span className={styles.plusIcon}>
                {/* Hidden if the image is missing, leaving the grey circle. */}
                <img
                  src={link.icon}
                  alt=""
                  width={32}
                  height={32}
                  onError={(e) => {
                    e.currentTarget.style.visibility = "hidden";
                  }}
                />
              </span>
              {link.label}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
