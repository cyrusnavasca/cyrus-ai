import { withBotId } from "botid/next/config";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  poweredByHeader: false,
};

// withBotId adds the rewrites the invisible challenge needs.
export default withBotId(nextConfig);
