"use client";

import { COPY } from "../lib/copy";
import { Header } from "./Header";
import { InputBar } from "./InputBar";
import { MessageList } from "./MessageList";
import { StatusBar } from "./StatusBar";
import styles from "./chat.module.css";
import { useChat } from "./useChat";

export function Chat() {
  const chat = useChat();
  return (
    <main className={styles.stage}>
      <div className={styles.phone}>
        <div className={styles.island} aria-hidden />
        <StatusBar budget={chat.budget} />
        <Header budget={chat.budget} onClear={chat.clear} />
        <MessageList
          messages={chat.messages}
          waitingSince={chat.waitingSince}
          notice={chat.notice}
          onRetry={chat.retry}
        />
        {chat.dead ? (
          <div className={styles.dead}>{COPY.dead}</div>
        ) : (
          <InputBar onSend={chat.send} busy={chat.waitingSince !== null} />
        )}
      </div>
      <p className={styles.disclaimerOutside}>{COPY.disclaimer}</p>
    </main>
  );
}
