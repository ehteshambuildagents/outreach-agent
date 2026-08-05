/**
 * The middleware's page-reachability decision, kept as a pure function so it can be
 * unit-tested without a Clerk edge session.
 *
 * PUBLIC LAUNCH: the Clerk `appAccess` publicMetadata gate is LIFTED. Reaching an
 * app page no longer depends on a per-account review flag, so a customer who has
 * just paid — and whose Clerk metadata carries no such flag — is never bounced away
 * from the product they bought. This is intentionally PAGE reachability only:
 *
 *   • product DATA stays gated server-side by require_approved_user;
 *   • spend stays capped by plan entitlements;
 *   • the Lemon Squeezy webhook grants that server-side approval the moment a
 *     subscription goes active, so a paying customer is unblocked end-to-end while
 *     an unpaid account that reaches a page still gets nothing from the backend.
 *
 * Returns null to ALLOW the page to render, or a path to redirect the visitor to.
 */
export function appRouteDecision({ isSignedIn }: { isSignedIn: boolean }): string | null {
  // Any authenticated account may reach the app; the backend is the real gate.
  if (isSignedIn) return null;
  // A logged-out visitor is sent to the marketing home.
  return "/";
}
