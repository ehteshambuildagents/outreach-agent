"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { useUser } from "@clerk/nextjs";
import { ArrowRight, Loader2, Sparkles, X } from "lucide-react";
import { useDemo } from "@/components/demo/demo-provider";
import { api, type Billing, type PlanCatalogEntry } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * The subscription conversion moment — shown ONCE, and only AFTER the visitor has
 * received real value, never on a bare timer and never while they are mid-flow.
 *
 * It is audience-adaptive because two very different people reach the app shell:
 *
 *   * a signed-in FREE member can actually transact, so the primary CTA opens the
 *     real Lemon Squeezy Checkout for the recommended plan;
 *   * a sandboxed DEMO visitor has no account and cannot check out, so the SAME
 *     polished card routes them to /pricing (their Gmail is already on the
 *     waitlist) — never a dead checkout button.
 *
 * Everything it displays — plan name, price, allowance — comes from the canonical
 * billing catalog (`GET /api/billing` → `catalog`), so no price is ever hardcoded
 * here. It never renders for an already-paid user, and never records any research
 * content: the only telemetry is the event name + audience + plan.
 */

// Persisted so a dismissal survives reloads; a short cooldown means we ask again
// eventually for a member who kept exploring, but not on every visit.
const DISMISS_KEY = "saqua_upgrade_prompt_dismissed_at";
const COOLDOWN_MS = 3 * 24 * 60 * 60 * 1000; // 3 days
// "Received value": for demo, two real agent turns (each does live research); for a
// member, at least one researched prospect (server-tracked). A dwell fallback so a
// slow-but-engaged session still sees it.
const DEMO_TURNS_TRIGGER = 2;
const MEMBER_PROSPECTS_TRIGGER = 1;
const DWELL_MS = 3.5 * 60 * 1000;

type UpgradeEvent = "displayed" | "dismissed" | "checkout_clicked";

function track(event: UpgradeEvent, audience: "member" | "demo", plan: string) {
  // No research content ever leaves here — just what happened, for whom, on which
  // plan. Any analytics layer can subscribe to this event; it is intentionally a
  // no-op-safe broadcast rather than a hard dependency.
  try {
    window.dispatchEvent(
      new CustomEvent("saqua:analytics", { detail: { kind: "upgrade_prompt", event, audience, plan } }),
    );
  } catch {
    /* analytics is best-effort and must never break the UI */
  }
}

function readDismissedAt(): number {
  try {
    const raw = window.localStorage.getItem(DISMISS_KEY);
    const n = raw ? Number.parseInt(raw, 10) : 0;
    return Number.isFinite(n) ? n : 0;
  } catch {
    return 0;
  }
}

function priceLabel(p: PlanCatalogEntry): string {
  const unit = p.currency === "USD" ? "$" : `${p.currency} `;
  const per = p.interval === "yearly" ? "/yr" : "/mo";
  return `${unit}${p.price}${per}`;
}

export function UpgradePrompt() {
  const { isDemo, turnsUsed } = useDemo();
  const { isSignedIn, isLoaded } = useUser();
  const router = useRouter();

  const [billing, setBilling] = useState<Billing | null>(null);
  const [open, setOpen] = useState(false);
  const [checkoutBusy, setCheckoutBusy] = useState(false);
  const [checkoutError, setCheckoutError] = useState("");

  const dismissed = useRef(false);
  const startedAt = useRef<number>(0);
  const busy = useRef(false); // an agent stream is running
  const typing = useRef(false); // the composer (or any text field) has focus
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const primaryRef = useRef<HTMLButtonElement | null>(null);
  const restoreFocusTo = useRef<HTMLElement | null>(null);
  // Demo turn count, kept in a ref the interval reads without re-subscribing.
  const demoTurns = useRef(0);
  useEffect(() => {
    demoTurns.current = turnsUsed;
  }, [turnsUsed]);

  const audience: "member" | "demo" = isDemo ? "demo" : "member";
  // A demo visitor authenticates via the same-origin cookie (no Clerk wait); a
  // member must wait for Clerk to hydrate or the /api/billing call 401s (the known
  // hydration race), so only fetch once we truly know who they are.
  const authReady = isDemo || (isLoaded && isSignedIn === true);

  // The plan we pitch: the server's recommended next tier, resolved against the
  // canonical catalog. Null => nothing to sell, so the prompt never shows.
  const plan: PlanCatalogEntry | null = (() => {
    const cat = billing?.catalog || [];
    if (!cat.length) return null;
    const want = billing?.recommended_upgrade;
    return cat.find((p) => p.plan === want) || cat[0];
  })();
  const alreadyPaid = billing?.is_paid === true;
  const canCheckout = audience === "member" && billing?.checkout_enabled === true;

  useEffect(() => {
    const at = readDismissedAt();
    dismissed.current = at > 0 && Date.now() - at < COOLDOWN_MS;
    startedAt.current = Date.now();
  }, []);

  // Load the canonical billing state once we know the caller. Cheap, read-only.
  useEffect(() => {
    if (!authReady) return;
    let alive = true;
    void api.billing().then((r) => {
      if (alive && r.ok) setBilling(r.data);
    });
    return () => {
      alive = false;
    };
  }, [authReady]);

  // Track composer focus and stream activity so the prompt never covers the
  // composer while the user is typing, nor interrupts a running agent turn.
  useEffect(() => {
    const isTextTarget = (el: EventTarget | null) => {
      const n = el as HTMLElement | null;
      if (!n || !n.tagName) return false;
      return (
        n.tagName === "TEXTAREA" ||
        n.tagName === "INPUT" ||
        n.isContentEditable === true
      );
    };
    const onFocusIn = (e: FocusEvent) => {
      if (isTextTarget(e.target)) typing.current = true;
    };
    const onFocusOut = () => {
      typing.current = false;
    };
    const onBusy = (e: Event) => {
      busy.current = Boolean((e as CustomEvent<boolean>).detail);
    };
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("focusout", onFocusOut);
    window.addEventListener("saqua:busy", onBusy as EventListener);
    return () => {
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("focusout", onFocusOut);
      window.removeEventListener("saqua:busy", onBusy as EventListener);
    };
  }, []);

  const show = useCallback(() => {
    if (dismissed.current || alreadyPaid || !plan) return;
    if (busy.current || typing.current) return; // never interrupt / cover the composer
    restoreFocusTo.current = (document.activeElement as HTMLElement) || null;
    setOpen(true);
    track("displayed", audience, plan.plan);
  }, [alreadyPaid, plan, audience]);

  // Engagement watch: a cheap interval that re-checks the value + idle conditions,
  // so the turn/prospect trigger and the dwell trigger share one path and a session
  // that arrives mid-flow is still measured.
  useEffect(() => {
    if (!billing || alreadyPaid || dismissed.current || open || !plan) return;
    const evaluate = () => {
      if (open || dismissed.current) return;
      const valued =
        audience === "demo"
          ? demoTurns.current >= DEMO_TURNS_TRIGGER
          : (billing.prospects_used ?? 0) >= MEMBER_PROSPECTS_TRIGGER;
      const dwelled = Date.now() - startedAt.current >= DWELL_MS;
      if (valued || dwelled) show();
    };
    evaluate();
    const id = window.setInterval(evaluate, 15_000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [billing, alreadyPaid, open, plan, audience]);

  const close = useCallback(() => {
    setOpen(false);
    dismissed.current = true;
    try {
      window.localStorage.setItem(DISMISS_KEY, String(Date.now()));
    } catch {
      /* ignore storage errors */
    }
    if (plan) track("dismissed", audience, plan.plan);
    restoreFocusTo.current?.focus?.();
  }, [audience, plan]);

  function onPrimary() {
    if (!plan || checkoutBusy) return;
    track("checkout_clicked", audience, plan.plan);
    if (!canCheckout) {
      // A demo visitor (or billing not live): send them to pricing, not a checkout
      // that would 401. Their Gmail is already on the waitlist.
      router.push("/pricing");
      return;
    }
    setCheckoutBusy(true);
    setCheckoutError("");
    void api.checkout(plan.plan).then((r) => {
      if (!r.ok) {
        setCheckoutBusy(false);
        setCheckoutError(r.error || "Could not start checkout. Please try again.");
        return;
      }
      window.location.href = r.data.url; // hand off to Lemon Squeezy hosted checkout
    });
  }

  // Focus the primary action on open, trap Tab within the dialog, and close on
  // Escape — a keyboard user is never stranded.
  useEffect(() => {
    if (!open) return;
    primaryRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        close();
        return;
      }
      if (e.key !== "Tab") return;
      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
        'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])',
      );
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  if (!open || !plan) return null;

  return createPortal(
    <div className="fixed inset-0 z-[110] grid place-items-center px-5">
      <button
        type="button"
        aria-label="Close"
        onClick={close}
        className="absolute inset-0 cursor-default bg-black/30 backdrop-blur-[2px]"
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="upgrade-prompt-title"
        aria-describedby="upgrade-prompt-body"
        className="demo-entry-content relative w-full max-w-sm rounded-2xl border border-border bg-card p-7 shadow-pop"
      >
        <button
          type="button"
          onClick={close}
          aria-label="Dismiss"
          className="absolute right-3 top-3 grid size-7 place-items-center rounded-md text-muted transition-colors hover:bg-black/[0.05] hover:text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <X className="size-4" />
        </button>

        <div className="grid size-11 place-items-center rounded-full bg-accent-soft text-accent">
          <Sparkles className="size-5" />
        </div>

        <h2 id="upgrade-prompt-title" className="mt-4 font-display text-xl font-semibold text-text">
          Ready to turn these results into outreach?
        </h2>

        <p id="upgrade-prompt-body" className="mt-2.5 text-sm leading-6 text-text-2">
          Upgrade to {plan.name} to unlock full research, saved campaigns, more qualified prospects,
          and email sending.
        </p>

        {/* Canonical plan facts — read from the billing catalog, never hardcoded. */}
        <div className="mt-4 flex items-baseline gap-2 rounded-lg border border-border bg-black/[0.02] px-3.5 py-2.5">
          <span className="font-display text-lg font-semibold text-text">{plan.name}</span>
          <span className="text-sm font-medium text-text-2">{priceLabel(plan)}</span>
          <span className="ml-auto text-xs text-muted">
            {plan.prospect_limit} researched prospects / mo
          </span>
        </div>

        {checkoutError && (
          <p className="mt-3 text-xs leading-5 text-[color:var(--danger,#b42318)]" role="alert">
            {checkoutError}
          </p>
        )}

        <button
          ref={primaryRef}
          type="button"
          onClick={onPrimary}
          disabled={checkoutBusy}
          className={cn(
            "mt-5 inline-flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-accent px-5 text-sm font-semibold text-white shadow-[0_1px_2px_rgba(79,90,247,.35)] transition-all hover:-translate-y-px hover:bg-accent-hi hover:shadow-[0_8px_22px_rgba(79,90,247,.32)]",
            "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
            "disabled:cursor-not-allowed disabled:opacity-70 disabled:hover:translate-y-0",
          )}
        >
          {checkoutBusy ? (
            <>
              <Loader2 className="size-4 animate-spin" /> Starting checkout…
            </>
          ) : (
            <>
              Upgrade to {plan.name} <ArrowRight className="size-4" />
            </>
          )}
        </button>

        <button
          type="button"
          onClick={close}
          className="mt-2.5 inline-flex h-10 w-full items-center justify-center rounded-lg px-5 text-sm font-medium text-text-2 transition-colors hover:text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {audience === "demo" ? "Continue the demo" : "Maybe later"}
        </button>
      </div>
    </div>,
    document.body,
  );
}
