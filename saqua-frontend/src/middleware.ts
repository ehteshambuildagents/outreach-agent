import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

// Pre-launch lockdown. The only thing a visitor can reach is the marketing site
// and the waitlist. Everything else — every authenticated app page AND the
// sign-in / sign-up routes — is redirected to the landing page, so there is no
// login path anywhere and no app page is reachable by any URL. The authenticated
// app UI still lives in the repo; it is being redesigned and is intentionally
// unreachable until then.
//
// Sole exception: Clerk user IDs listed in FOUNDER_USER_IDS may still reach the
// app (e.g. to run the live Gmail re-consent test). Keyed on the Clerk-verified
// session userId, so it cannot be spoofed; unset => no exception, full lockdown.
//
// To reopen the app later: add the app routes back as protected (restore the
// `auth()` / redirectToSignIn gate) and return sign-in/sign-up to this list.
const isPublicRoute = createRouteMatcher([
  "/",
  "/about(.*)",
  "/pricing(.*)",
  "/contact(.*)",
  "/privacy(.*)",
  "/terms(.*)",
  // TEMPORARY re-auth allowance: lets an existing user (the founder) sign back in
  // during the locked-down phase. Only /sign-in is opened — /sign-up stays closed
  // — and reaching any app page still requires the FOUNDER_USER_IDS gate below, so
  // signing in grants a session but NOT app access to anyone else. Remove this
  // line once the founder's session is re-established.
  "/sign-in(.*)",
]);

// Founder bypass allowlist: comma-separated Clerk user IDs. Empty when the env
// var is unset, so the default is the full lockdown with no exceptions.
const FOUNDER_USER_IDS = (process.env.FOUNDER_USER_IDS || "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

export default clerkMiddleware(
  async (auth, request) => {
    if (isPublicRoute(request)) {
      return;
    }
    // Founder exception: a listed, Clerk-verified user may reach the app while it
    // is locked down. Everyone else — logged in or not — is redirected.
    const { userId } = await auth();
    if (userId && FOUNDER_USER_IDS.includes(userId)) {
      return;
    }
    // Not public and not a founder → the landing page. Covers direct-URL
    // navigation to any app page and to /sign-in or /sign-up.
    return NextResponse.redirect(new URL("/", request.url));
  },
  {
    // Clerk's bot protection uses Cloudflare Turnstile. Let Clerk inject the
    // CSP directives it needs, including challenges.cloudflare.com for scripts
    // and frames, so CAPTCHA does not depend on an external edge header.
    contentSecurityPolicy: {
      directives: {
        "connect-src": ["https://challenges.cloudflare.com"],
        "font-src": ["self", "https://fonts.gstatic.com"],
        "frame-src": ["https://challenges.cloudflare.com"],
        "script-src": ["https://challenges.cloudflare.com"],
        "style-src": ["https://fonts.googleapis.com"],
      },
    },
  },
);

export const config = {
  matcher: ["/((?!api|_next|.*\\..*).*)"],
};
