import { PHASE_DEVELOPMENT_SERVER } from "next/constants.js";

const nextConfig = (phase) => ({
  reactStrictMode: true,
  // Keep the dev server cache separate from production builds. Running
  // `next build` while `next dev` was still active rewrote `.next/static/css`,
  // leaving dev HTML pointing at a missing `/app/layout.css` bundle.
  distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next",
});

export default nextConfig;
