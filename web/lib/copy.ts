// Every string a visitor reads, in one place.
export const COPY = {
  name: "CyrusGPT",
  placeholder: "iMessage",
  waking: "CyrusGPT is waking up…",
  limitDay: "You've hit today's limit — try again tomorrow",
  limitMinute: "slow down a sec",
  dead: "CyrusGPT's phone died 🪫 Back tomorrow.",
  disclaimer: "AI trained on my texts. It's not me. Don't share anything private.",
  delivered: "Delivered",
  notDelivered: "Not Delivered",
  clearConfirm: "Clear this conversation?",
} as const;

// After this long, the wait is a cold start rather than typing.
export const WAKING_MS = 12_000;
