"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isExhausted } from "../lib/budget";
import { sendTurns } from "../lib/chatClient";
import { COPY } from "../lib/copy";
import type { Budget } from "../lib/limits";
import { type Message, toHistory } from "../lib/thread";

const LEGACY_STORAGE_KEY = "cyrusgpt:thread:v1";
// crypto.randomUUID is missing on non-HTTPS origins and iOS < 15.4.
const newId = () =>
  typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [dead, setDead] = useState(false);
  const [waitingSince, setWaitingSince] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // The latest messages, for async code that outlives a render.
  const ref = useRef<Message[]>([]);
  const busy = useRef(false);

  const commit = useCallback((next: Message[]) => {
    ref.current = next;
    setMessages(next);
  }, []);
  const patch = useCallback(
    (id: string, change: Partial<Message>) =>
      commit(ref.current.map((m) => (m.id === id ? { ...m, ...change } : m))),
    [commit],
  );

  const refreshBudget = useCallback(async () => {
    try {
      const r = await fetch("/api/budget");
      if (!r.ok) return;
      const b = (await r.json()) as Budget;
      setBudget(b);
      setDead((d) => d || isExhausted(b));
    } catch {
      // The meter is a nicety; the page works without it.
    }
  }, []);

  // The thread lives only in memory, so a refresh starts a new conversation.
  useEffect(() => {
    try {
      // Drop the copy that earlier versions of this page saved.
      localStorage.removeItem(LEGACY_STORAGE_KEY);
    } catch {
      // Blocked storage: nothing was saved there either.
    }
    void refreshBudget();
  }, [refreshBudget]);

  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || busy.current) return;
      busy.current = true;
      setNotice(null);
      const mine: Message = { id: newId(), me: true, text, at: Date.now(), status: "sending" };
      commit([...ref.current, mine]);
      setWaitingSince(Date.now());

      const outcome = await sendTurns(toHistory(ref.current), {
        // Wrapped, not passed bare: some browsers throw "Illegal invocation"
        // when fetch is called detached from window.
        fetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
        sleep,
        onAccepted: () => patch(mine.id, { status: "delivered" }),
      });

      setWaitingSince(null);
      busy.current = false;
      if (outcome.kind === "reply") {
        commit([...ref.current, { id: newId(), me: false, text: outcome.text, at: Date.now(), status: "received" }]);
      } else {
        patch(mine.id, { status: "failed" });
        if (outcome.kind === "limit") setNotice(outcome.reason === "minute" ? COPY.limitMinute : COPY.limitDay);
        if (outcome.kind === "budget") setDead(true);
      }
      void refreshBudget();
    },
    [commit, patch, refreshBudget],
  );

  const retry = useCallback(
    (id: string) => {
      const m = ref.current.find((x) => x.id === id);
      if (!m || m.status !== "failed" || busy.current) return;
      commit(ref.current.filter((x) => x.id !== id));
      void send(m.text);
    },
    [commit, send],
  );

  const clear = useCallback(() => {
    if (busy.current) return;
    commit([]);
    setNotice(null);
  }, [commit]);

  return { messages, budget, dead, waitingSince, notice, send, retry, clear };
}
