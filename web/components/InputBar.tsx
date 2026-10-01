"use client";

import { useRef, useState } from "react";
import { COPY } from "../lib/copy";
import { MAX_CHARS } from "../lib/validate";
import styles from "./chat.module.css";

const COUNTER_AFTER = 250;

export function InputBar({ onSend, busy }: { onSend: (text: string) => void; busy: boolean }) {
  const [text, setText] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const hasText = text.trim().length > 0;

  const submit = () => {
    if (!hasText || busy) return;
    onSend(text);
    setText("");
    if (box.current) box.current.style.height = "auto";
  };

  return (
    <form
      className={styles.inputBar}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <button type="button" className={styles.plus} disabled tabIndex={-1} aria-hidden>
        +
      </button>
      <div className={styles.field}>
        <textarea
          ref={box}
          rows={1}
          value={text}
          maxLength={MAX_CHARS}
          placeholder={COPY.placeholder}
          aria-label="Message"
          onChange={(e) => {
            setText(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
          }}
          onKeyDown={(e) => {
            // isComposing: Enter that confirms an IME candidate is not a send.
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {hasText && (
          <button type="submit" className={styles.send} disabled={busy} aria-label="Send">
            <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
              <path d="M7 12V2M2.5 6.5 7 2l4.5 4.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        )}
      </div>
      {text.length > COUNTER_AFTER && <span className={styles.counter}>{MAX_CHARS - text.length}</span>}
    </form>
  );
}
