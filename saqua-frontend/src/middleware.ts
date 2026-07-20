import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

// Pre-launch lockdown. The only thing a visitor can reach is the marketing site
// and the waitlist. Everything else — every authenticated app page AND the
// sign-in / sign-up routes — is redirected to the landing page, so there is no
// login path anywhere and no app page is reachable by any URL, for anyone
// (existing sessions included). The authenticated app UI still lives in the repo;
// it is being redesigned and is intentionally unreachable until then.
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
]);

export default clerkMiddleware(
  async (auth, request) => {
    if (isPublicRoute(request)) {
      return;
    }
    // Not public → the landing page. Covers direct-URL navigation to any app
    // page and to /sign-in or /sign-up.
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
