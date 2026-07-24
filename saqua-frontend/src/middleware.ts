import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse, type NextRequest } from "next/server";

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

// A live demo session grants PAGE reachability (not data): the readable expiry
// cookie carries the token's expiry epoch, so the edge allows app routes only
// while it is in the future. The HttpOnly token cookie is the one the backend
// actually trusts; this is purely for routing.
function hasLiveDemoCookie(request: NextRequest): boolean {
  const exp = request.cookies.get("saqua_demo_exp")?.value;
  if (!exp) return false;
  const epoch = Number.parseInt(exp, 10);
  return Number.isFinite(epoch) && epoch * 1000 > Date.now();
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
    // Sandboxed demo visitor: a present, unexpired demo cookie lets the real app
    // pages RENDER (reachability only). This is deliberately a cheap structural
    // check — the edge can't verify the HMAC and doesn't need to: every /api call
    // the pages make is authorised at the backend, which alone can mint or read
    // demo data. A forged cookie loads an empty shell and nothing more.
    if (hasLiveDemoCookie(request)) {
      return;
    }
    const { userId, sessionClaims } = await auth();
    if (userId && hasAppAccess(sessionClaims)) {
      return;
    }
    // Not public, not demo, not review-flagged → the landing page. Covers direct-
    // URL navigation to any app page and to /sign-in or /sign-up.
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
