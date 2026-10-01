import { createHmac, timingSafeEqual } from "node:crypto";

// The browser polls with a ticket, never a bare Modal call id: /api/poll only
// forwards ids this server issued, so it cannot be used to read arbitrary calls.

const ID = /^[A-Za-z0-9_-]{1,128}$/;

const mac = (id: string, secret: string) =>
  createHmac("sha256", secret).update(id).digest("base64url");

export function signTicket(callId: string, secret: string): string {
  return `${callId}.${mac(callId, secret)}`;
}

export function verifyTicket(ticket: string, secret: string): string | null {
  const dot = ticket.lastIndexOf(".");
  if (dot <= 0) return null;
  const id = ticket.slice(0, dot);
  if (!ID.test(id)) return null;
  const given = Buffer.from(ticket.slice(dot + 1));
  const want = Buffer.from(mac(id, secret));
  if (given.length !== want.length || !timingSafeEqual(given, want)) return null;
  return id;
}
