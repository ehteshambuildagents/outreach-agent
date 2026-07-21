import { PHASE_DEVELOPMENT_SERVER } from "next/constants.js";

const nextConfig = (phase) => ({
  reactStrictMode: true,
  // Keep the dev server cache separate from production builds. Running
  // `next build` while `next dev` was still active rewrote `.next/static/css`,
  // leaving dev HTML pointing at a missing `/app/layout.css` bundle.
  distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next",
  // The legacy static policy lived at /privacy.html (now 404 under the Next app).
  // Resolve that old URL to the canonical policy page so nothing dangles. The path
  // has a dot, so the middleware matcher (/((?!api|_next|.*\..*).*)) skips it and
  // this redirect applies cleanly.
  async redirects() {
    return [
      { source: "/privacy.html", destination: "/privacy", permanent: true },
    ];
  },
});

export default nextConfig;
