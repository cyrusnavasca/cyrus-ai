import { getDeps } from "../../../lib/deps";
import { handleBudget } from "../../../lib/handlers";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  const d = getDeps();
  return d.ok ? handleBudget(d.deps) : Response.json({ error: "not configured" }, { status: 503 });
}
