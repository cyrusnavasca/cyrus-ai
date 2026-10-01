import { initBotId } from "botid/client/core";

// Only the route that spends GPU is protected; poll and budget are free.
initBotId({
  protect: [{ path: "/api/send", method: "POST" }],
});
