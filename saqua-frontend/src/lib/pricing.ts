/**
 * Pricing → checkout mapping and the CTA decision, kept free of React and Next so
 * both the marketing /pricing page and its unit tests can import it directly.
 *
 * The public page markets the two self-serve tiers as "Starter" and "Growth"; the
 * backend (server/billing_api.py) knows them by their canonical plan ids "pro" and
 * "max" — the SAME ids the settings page and the in-app upgrade prompt already send
 * to POST /api/billing/checkout. This module is the single place that name→id
 * mapping lives, so the pricing page can never drift from the backend contract.
 *
 * Enterprise has no checkout plan: it stays on the sales-assisted "Talk to us"
 * (contact) flow, and this module returns `{ kind: "contact" }` for it.
 */

export type CheckoutPlanId = "pro" | "max";

/** Marketing plan name → canonical backend plan id (POST /api/billing/checkout). */
export const CHECKOUT_PLAN_ID: Record<string, CheckoutPlanId> = {
  Starter: "pro",
  Growth: "max",
};

/** The canonical backend plan id for a marketing plan name, or null when the plan
 *  is not self-serve purchasable (Enterprise, Free). */
export function checkoutPlanId(name: string): CheckoutPlanId | null {
  return CHECKOUT_PLAN_ID[name] ?? null;
}

/** Internal path sign-in should return a logged-out visitor to, so checkout resumes
 *  for the plan they picked. The `checkout` flag is read on the pricing page mount. */
export function checkoutReturnPath(planId: CheckoutPlanId): string {
  return `/pricing?checkout=${planId}`;
}

/** The sign-in URL that carries the return path, so a logged-out visitor lands back
 *  on /pricing with the checkout resuming after they authenticate. */
export function signInUrl(planId: CheckoutPlanId): string {
  return `/sign-in?redirect_url=${encodeURIComponent(checkoutReturnPath(planId))}`;
}

/**
 * The plan to auto-resume from a `?checkout=<planId>` return flag (set on the
 * /pricing return path after sign-in / sign-up). Only the two canonical self-serve
 * ids resume; anything else (tampered, stale) is ignored, so a bad value can never
 * kick off an unexpected checkout.
 */
export function resumeCheckoutPlan(search: string): CheckoutPlanId | null {
  const value = new URLSearchParams(search).get("checkout");
  return value === "pro" || value === "max" ? value : null;
}

export interface CtaAuth {
  /** Clerk has finished hydrating and `isSignedIn` is now trustworthy. */
  isLoaded: boolean;
  isSignedIn: boolean;
}

export type CtaAction =
  | { kind: "contact" } // Enterprise → talk to us
  | { kind: "wait" } // auth still hydrating; ignore the click (button is disabled)
  | { kind: "signin"; url: string } // logged out → sign in, then return to checkout
  | { kind: "checkout"; planId: CheckoutPlanId }; // logged in → start real checkout

/**
 * Decide what a pricing CTA click should do:
 *   • a plan with no checkout id (Enterprise) always routes to contact;
 *   • a paid tier waits while Clerk hydrates so we never misread auth state;
 *   • a logged-out visitor is sent through sign-in and returned to checkout;
 *   • a signed-in member starts the real Lemon Squeezy checkout.
 *
 * Pure and synchronous, so the pricing page and the tests share one source of truth.
 */
export function resolveCta(planName: string, auth: CtaAuth): CtaAction {
  const planId = checkoutPlanId(planName);
  if (!planId) return { kind: "contact" };
  if (!auth.isLoaded) return { kind: "wait" };
  if (!auth.isSignedIn) return { kind: "signin", url: signInUrl(planId) };
  return { kind: "checkout", planId };
}
