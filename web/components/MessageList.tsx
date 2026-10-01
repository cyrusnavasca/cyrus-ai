"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { COPY, WAKING_MS } from "../lib/copy";
import { formatTimeHeader, lastDeliveredId, layout, type Message } from "../lib/thread";
import styles from "./chat.module.css";

type Props = {
  messages: Message[];
  waitingSince: number | null;
  notice: string | null;
  onRetry: (id: string) => void;
};

const cx = (...names: Array<string | false>) => names.filter(Boolean).join(" ");

export function MessageList({ messages, waitingSince, notice, onRetry }: Props) {
  const end = useRef<HTMLDivElement>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (waitingSince === null) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [waitingSince]);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [messages, waitingSince, notice]);

  const rows = layout(messages, (at) => formatTimeHeader(at, Date.now()));
  const delivered = lastDeliveredId(messages);
  const waking = waitingSince !== null && now - waitingSince >= WAKING_MS;

  return (
    <div className={styles.log} role="log" aria-live="polite">
      {rows.map((row) =>
        row.type === "time" ? (
          <div key={row.key} className={styles.time}>
            {row.label}
          </div>
        ) : (
          <Fragment key={row.key}>
            <div className={cx(styles.row, row.message.me ? styles.rowMe : styles.rowThem, row.first && styles.first)}>
              <div className={cx(styles.bubble, row.message.me ? styles.me : styles.them, row.tail && styles.tail)}>
                {row.message.text}
              </div>
              {row.message.status === "failed" && (
                <button type="button" className={styles.failedIcon} onClick={() => onRetry(row.message.id)} aria-label="Retry">
                  !
                </button>
              )}
            </div>
            {row.message.id === delivered && <div className={styles.receipt}>{COPY.delivered}</div>}
            {row.message.status === "failed" && (
              <button type="button" className={styles.notDelivered} onClick={() => onRetry(row.message.id)}>
                {COPY.notDelivered}
              </button>
            )}
          </Fragment>
        ),
      )}
      {waitingSince !== null && (
        <>
          <div className={cx(styles.row, styles.rowThem, styles.first)}>
            <div className={cx(styles.bubble, styles.them, styles.tail, styles.typing)} aria-label="CyrusGPT is typing">
              <span />
              <span />
              <span />
            </div>
          </div>
          {waking && <div className={styles.caption}>{COPY.waking}</div>}
        </>
      )}
      {notice && <div className={styles.notice}>{notice}</div>}
      <div ref={end} />
    </div>
  );
}
