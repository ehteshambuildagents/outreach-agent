import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

// Pre-launch lockdown. The only thing a visitor can reach is the marketing site
// and the waitlist; every authenticated app page redirects to the landing page.
//
// Review-access exception (TEMPORARY, for Google OAuth verification): a signed-in
// user whose Clerk publicMetadata carries `appAccess: true` may reach the app, so a
// Google reviewer can exercise the Gmail consent + reply-detection flow. The flag
// lives server-side on the Clerk user record — no env var, no secret — surfaced to
// the edge via a session-token claim (Dashboard → Sessions → edit session token:
// {"appAccess": "{{user.public_metadata.appAccess}}"}). It grants PAGE reachability
// only; actual data is still gated by the DB approved-users store
// (server require_approved_user), so a reviewer needs BOTH the flag and DB approval.
// /sign-in is reopened so the reviewer can log in; /sign-up stays closed.
//
// To re-seal after verification: remove "/sign-in(.*)" and the appAccess check
// below, and clear the flag from the reviewer's Clerk user. Full step-by-step
// checklist (code + Clerk + DB): see REVIEWER_ACCESS_RESEAL.md at the repo root.
const isPublicRoute = createRouteMatcher([
  "/",
  "/about(.*)",
  "/pricing(.*)",
  "/contact(.*)",
  "/demo(.*)",
  "/privacy(.*)",
  "/terms(.*)",
  "/sign-in(.*)",
]);

// /sign-in must ALWAYS render, for anyone, regardless of PRELAUNCH state or
// approval status — authentication has to be reachable before any per-account
// approval logic can check who the visitor is. This is deliberately a separate,
// first-checked guard (not just a `isPublicRoute` entry) so the login path can
// never again be closed by accident, e.g. by reordering or by pruning the public
// list the way removing the founder bypass did.
const isSignInRoute = createRouteMatcher(["/sign-in(.*)"]);

// True when the signed-in user carries publicMetadata.appAccess === true. Accepts
// the boolean or its string form (the claim's type depends on the session-token
// template) and tolerates it arriving either as a top-level `appAccess` claim or
// nested under a `metadata` claim, so it works whichever way the token is shaped.
function hasAppAccess(claims: unknown): boolean {
  if (!claims || typeof claims !== "object") return false;
  const c = claims as Record<string, unknown>;
  const meta = c["metadata"];
  const nested =
    meta && typeof meta === "object" ? (meta as Record<string, unknown>)["appAccess"] : undefined;
  const v = c["appAccess"] ?? nested;
  return v === true || v === "true";
}

export default clerkMiddleware(
  async (auth, request) => {
    // Auth must be reachable before anything else can gate the visitor.
    if (isSignInRoute(request)) {
      return;
    }
    if (isPublicRoute(request)) {
      return;
    }
    const { userId, sessionClaims } = await auth();
    if (userId && hasAppAccess(sessionClaims)) {
      return;
    }
    // Not public and not review-flagged → the landing page. Covers direct-URL
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
