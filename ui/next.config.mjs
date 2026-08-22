/** @type {import('next').NextConfig} */
// The Content-Security-Policy and X-Frame-Options are NOT set here. They carry a per-request
// script nonce, which a static `headers()` table cannot express, so `proxy.ts` owns them and
// builds them from `lib/csp.mjs`. Setting the policy in both places would hand the browser two
// policies to intersect, and the stricter one wins on every directive, which would reinstate the
// nonce-less `script-src` that leaves this console rendering as dead markup.
//
// What IS here are the two refusals. `next build` and `next start` both evaluate this file at
// module scope, so:
//
//   * a layout that has lost its `force-dynamic` (and therefore cannot carry the nonce) fails the
//     build instead of shipping a console whose controls silently do nothing, and
//   * an embedding variable that is set but names nothing fails the build/boot instead of
//     inheriting the default, which would make a deliberately emptied allowlist indistinguishable
//     from one that went missing.
import { readFileSync } from "node:fs";

import { assertEmbedPolicyConfigured, assertHydratableCsp } from "./lib/csp.mjs";

assertHydratableCsp(readFileSync(new URL("./app/layout.tsx", import.meta.url), "utf8"));
assertEmbedPolicyConfigured(process.env);

// Mount the UI (and its assets) under a reverse-proxy sub-path via NEXT_PUBLIC_BASE_PATH
// (for example "/agent"). Blank keeps the standalone deployment unchanged.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

const nextConfig = {
  reactStrictMode: true,
  ...(basePath ? { basePath, assetPrefix: basePath } : {}),
  async headers() {
    // Only the headers a static table can express correctly. Anything per-request lives in
    // `proxy.ts`.
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default nextConfig;
