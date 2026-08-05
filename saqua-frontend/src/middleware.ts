import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse, type NextRequest } from "next/server";
import { appRouteDecision } from "@/lib/access-gate";

// PUBLIC LAUNCH access model. The former pre-launch lockdown — which redirected
// every authenticated app page away unless the signed-in user carried the Clerk
// `appAccess` publicMetadata flag — is LIFTED here: reaching an app page no longer
// depends on that per-account flag, so a customer who has just paid is never
// bounced off the product they bought. Page reachability is granted to any
// authenticated account; product DATA stays gated server-side by
// require_approved_user and spend by plan entitlements, and the Lemon Squeezy
// webhook grants that server-side approval the instant a subscription goes active.
// The routing decision itself lives in lib/access-gate.ts (appRouteDecision) so it
// can be unit-tested. /sign-in is always reachable; the /sign-up route is reachable
// for the paid-checkout funnel but the page keeps itself gated (buyers only).
const isPublicRoute = createRouteMatcher([
  "/",
  "/about(.*)",
  "/pricing(.*)",
  "/contact(.*)",
  "/demo(.*)",
  "/privacy(.*)",
  "/terms(.*)",
  "/sign-in(.*)",
  // The post-payment confirmation page is deliberately PUBLIC: Lemon Squeezy returns
  // the browser here after a successful purchase, and it must render even before the
  // buyer's session claims have caught up. It carries a "Continue to dashboard"
  // button and exposes no data of its own.
  "/checkout(.*)",
  // The sign-up ROUTE is reachable so the paid-checkout funnel can create an
  // account; the sign-up PAGE itself stays gated (it renders the form only for a
  // visitor arriving with a valid checkout return path, and otherwise sends a
  // pre-launch visitor to the waitlist). Creating an account grants no app access
  // on its own: data stays gated by the backend, so reopening the route does not
  // reopen the product.
  "/sign-up(.*)",
]);

// /sign-in must ALWAYS render, for anyone, regardless of PRELAUNCH state or
// approval status — authentication has to be reachable before any per-account
// approval logic can check who the visitor is. This is deliberately a separate,
// first-checked guard (not just a `isPublicRoute` entry) so the login path can
// never again be closed by accident, e.g. by reordering or by pruning the public
// list the way removing the founder bypass did.
const isSignInRoute = createRouteMatcher(["/sign-in(.*)"]);

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
    // Public launch: any authenticated account reaches the app (backend gates data
    // and spend); a logged-out visitor goes to the marketing home. The Clerk
    // appAccess flag is no longer consulted, so a just-paid buyer is never blocked.
    const { userId } = await auth();
    const target = appRouteDecision({ isSignedIn: Boolean(userId) });
    if (target === null) {
      return;
    }
    return NextResponse.redirect(new URL(target, request.url));
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
