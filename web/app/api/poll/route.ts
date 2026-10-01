import { getDeps } from "../../../lib/deps";
import { handlePoll } from "../../../lib/handlers";

export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<Response> {
  const d = getDeps();
  return d.ok ? handlePoll(req, d.deps) : Response.json({ error: "not configured" }, { status: 503 });
}
