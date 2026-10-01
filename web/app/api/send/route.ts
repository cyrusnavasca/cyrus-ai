import { getDeps } from "../../../lib/deps";
import { handleSend } from "../../../lib/handlers";

export const dynamic = "force-dynamic";
// Covers modal.ts's 25s spawn timeout so the charge is never cut off mid-call.
export const maxDuration = 30;

export async function POST(req: Request): Promise<Response> {
  const d = getDeps();
  return d.ok ? handleSend(req, d.deps) : Response.json({ error: "not configured" }, { status: 503 });
}
