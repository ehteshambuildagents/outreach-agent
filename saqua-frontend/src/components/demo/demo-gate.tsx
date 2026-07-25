"use client";

import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { motion, useReducedMotion } from "framer-motion";
import { AlertCircle, ArrowRight, Check, Loader2, Lock } from "lucide-react";
import { Logo } from "@/components/ui/logo";
import { cn } from "@/lib/utils";

/**
 * The one small step between /demo and the real workspace.
 *
 * A visitor gives a personal Gmail address (no account, no password). That
 * mints a signed, short-lived demo session cookie via ``POST /api/demo/session``,
 * passing the SAME abuse gate as a live run (honeypot, Gmail-only rule, per-IP +
 * per-email caps, global daily budget, soft waitlist add).
 *
 * The staged transition starts the INSTANT the button is clicked, so entering
 * feels like the product booting rather than a request spinner: the first
 * stages advance on their own while the mint runs, and the LAST stage only
 * completes once the server confirms, then a FULL navigation into ``/ai`` lets
 * the middleware see the new cookie and boot the shell in demo mode. If the
 * mint is refused, the overlay steps aside and the honest block message shows.
 *
 * The Gmail-only rule is enforced inline before submit (small hint, disabled
 * button), and again by the backend (``gmail_only`` state) for anyone bypassing
 * the form.
 */

/** An inline failure under the button. `retry` marks the transient kind, where
 *  trying again in a moment genuinely works, so we offer the action. */
type Failure = { message: string; retry: boolean };

/**
 * One message per real cause. A temporary pending lock, a genuine daily limit,
 * a rejected address, and a server fault are four different situations, and
 * answering all of them with "that's all for now" is how a visitor concludes
 * the product is broken. `onWaitlist` is appended only when the visitor's email
 * actually reached the waitlist (the gate adds it before minting), so we never
 * ask for an address they already gave.
 */
function describe(status: number, payload: Record<string, unknown>): Failure {
  const state = (payload.state as string) || "error";
  const scope = payload.scope as string | undefined;
  const server = (payload.message as string) || "";
  const onWaitlist = " Your Gmail is already on the waitlist, so nothing is lost.";

  if (state === "rate_limited" && scope === "burst") {
    return {
      message: "We couldn't start the demo just now. Please try again in a few seconds.",
      retry: true,
    };
  }
  if (state === "rate_limited") {
    return { message: (server || "You've reached today's demo limit.") + onWaitlist, retry: false };
  }
  if (state === "capacity") {
    return { message: (server || "Today's demo capacity is full.") + onWaitlist, retry: false };
  }
  if (state === "gmail_only" || state === "need_email") {
    return { message: server || "That address didn't look right.", retry: false };
  }
  if (status === 0) {
    return { message: "We couldn't reach the demo. Check your connection and try again.", retry: true };
  }
  return {
    message: server || "Something went wrong starting the demo. Please try again in a moment.",
    retry: true,
  };
}

// The staged entry sequence. Earlier stages advance on a timer; the last check
// lands only when the session is really minted, so the sequence is never a lie
// about progress, and the whole handoff stays a bit under ~3s on a normal mint.
const STAGES = ["Research environment…", "Preparing your workspace…", "Launching Saqua…"];
const STAGE_MS = 700;

export function DemoGate() {
  const reduced = useReducedMotion();
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState(""); // honeypot
  const [entering, setEntering] = useState(false);
  const [stage, setStage] = useState(0);
  const [failure, setFailure] = useState<Failure | null>(null);
  const timers = useRef<number[]>([]);
  // Single-flight guard. A ref, NOT the `entering` state: state updates are not
  // synchronous, so two submits in the same tick (a double-click, or Enter plus
  // a click) both read the old value and both fire a request. The second then
  // trips the per-IP burst limiter, and because the two responses race, a 429
  // arriving after the 200 would tear down a session that had already been
  // minted. One click must produce exactly one request.
  const inFlight = useRef(false);

  const trimmed = email.trim().toLowerCase();
  const looksComplete = /.+@.+\..+/.test(trimmed);
  const isGmail = trimmed.endsWith("@gmail.com");
  const canStart = looksComplete && isGmail;
  // The moment a complete non-Gmail address is typed, explain the rule quietly
  // instead of letting a dead button do the talking.
  const gmailHint = looksComplete && !isGmail;

  function clearTimers() {
    timers.current.forEach((t) => window.clearTimeout(t));
    timers.current = [];
  }

  async function start(e: React.FormEvent) {
    e.preventDefault();
    if (inFlight.current || !canStart) return;
    inFlight.current = true;
    setFailure(null);

    // Curtain up immediately: the boot sequence plays WHILE the mint runs.
    setEntering(true);
    setStage(0);
    const t0 = performance.now();
    timers.current = STAGES.slice(0, -1).map((_, i) =>
      window.setTimeout(() => setStage((s) => Math.max(s, i + 1)), STAGE_MS * (i + 1)),
    );

    let fail: Failure | null = null;
    let ok = false;
    try {
      const res = await fetch("/api/demo/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ email, company }),
      });
      let payload: Record<string, unknown> = {};
      try {
        payload = await res.json();
      } catch {
        /* fall through */
      }
      ok = res.ok && Boolean(payload.active);
      if (!ok) fail = describe(res.status, payload);
    } catch {
      fail = describe(0, {});
    }

    if (!ok) {
      // Release the guard so the visitor can genuinely retry, and put them back
      // in the same card rather than replacing the page with an error.
      inFlight.current = false;
      clearTimers();
      setEntering(false);
      setStage(0);
      setFailure(fail);
      return;
    }

    // Session is real. Let the sequence finish (never rushing it below the
    // full run), stamp the last check, then hand the page to the workspace.
    const elapsed = performance.now() - t0;
    const wait = Math.max(0, STAGE_MS * STAGES.length - elapsed);
    timers.current.push(
      window.setTimeout(() => {
        setStage(STAGES.length);
        timers.current.push(
          window.setTimeout(() => window.location.assign("/ai"), 500),
        );
      }, wait),
    );
  }

  return (
    <div className="w-full">
      {/* The page's primary conversion point: a touch more presence than a
          plain card (accent hairline, deeper shadow) while staying minimal. */}
      <form
        onSubmit={start}
        className="glass rounded-2xl border border-accent-line p-6 shadow-pop backdrop-blur-md md:p-7"
      >
        <label htmlFor="demo-email" className="block text-[15px] font-semibold text-text">
          Enter your personal Gmail to begin.
        </label>

        {/* Honeypot: hidden; a bot that fills it gets a benign non-start. */}
        <input
          type="text"
          name="company"
          value={company}
          onChange={(e) => setCompany(e.target.value)}
          tabIndex={-1}
          autoComplete="off"
          aria-hidden="true"
          className="pointer-events-none absolute left-[-9999px] size-0 opacity-0"
        />

        <div className="mt-3 flex flex-col gap-2.5 sm:flex-row">
          <input
            id="demo-email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@gmail.com"
            aria-label="Personal Gmail address"
            disabled={entering}
            className="h-12 w-full min-w-0 flex-1 rounded-lg border border-border-strong bg-white px-4 text-sm text-text outline-none transition-all placeholder:text-faint focus:border-accent-line focus:shadow-[0_0_0_4px_var(--accent-soft)] disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={entering || !canStart}
            className={cn(
              "inline-flex h-12 shrink-0 items-center justify-center gap-2 rounded-lg bg-accent px-5 text-sm font-semibold text-white shadow-[0_1px_2px_rgba(79,90,247,.35)] transition-all hover:-translate-y-px hover:bg-accent-hi hover:shadow-[0_8px_22px_rgba(79,90,247,.32)]",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
              "disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0",
            )}
          >
            Enter the demo <ArrowRight className="size-4" />
          </button>
        </div>

        {/* Guidance, not a warning: the button is already disabled, and amber
            text on the cream canvas fails contrast. Quiet ink reads better. */}
        {gmailHint && (
          <p className="mt-2 text-xs leading-5 text-text-2" role="status">
            The demo takes personal Gmail addresses only for now. Any email works
            for the waitlist, and every provider gets full access at launch.
          </p>
        )}

        {/* Failures stay INSIDE this card: a second full-width panel underneath
            reads as a dead end, and the visitor already gave us their address,
            so there is deliberately no second email field anywhere on /demo. */}
        {failure && (
          <div
            className="mt-3 flex items-start gap-2 rounded-lg border border-border bg-black/[0.02] px-3 py-2.5"
            role="alert"
          >
            <AlertCircle className="mt-0.5 size-3.5 shrink-0 text-muted" />
            <p className="text-xs leading-5 text-text-2">
              {failure.message}
              {failure.retry && (
                <button
                  type="submit"
                  className="ml-1.5 font-semibold text-accent underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  Try again
                </button>
              )}
            </p>
          </div>
        )}

        <p className="mt-3 flex items-center justify-center gap-1.5 text-center text-xs text-muted">
          <Lock className="size-3 shrink-0" /> No account. Five messages. Drafts only. Your email
          also holds your waitlist spot.
        </p>
      </form>

      {/* ── Staged transition into the workspace ─────────────────────────
          Covers the page the moment the button is clicked, builds the session
          in three visible steps, then the /ai navigation replaces the page. If
          the mint is refused it unmounts to the honest message. Two hard-won
          constraints: the sheet is static and only the content animates
          (.demo-entry-content), because animation timelines freeze in
          throttled/hidden tabs and a sheet fading from 0 would strand an
          invisible cover; and it renders in a PORTAL to <body>, because the
          gate sits inside a transformed Reveal wrapper, which would otherwise
          become the containing block and clip "fixed" to the card's box. */}
      {entering &&
        createPortal(
          <div
            className="fixed inset-0 z-[100] grid place-items-center bg-bg"
          >
            <div aria-hidden className="hero-glow-cool pointer-events-none absolute inset-x-0 top-0 h-[480px]" />
            <div className="demo-entry-content relative flex w-full max-w-xs flex-col items-center gap-8 px-6">
              <motion.span
                className="relative"
                animate={reduced ? undefined : { scale: [1, 1.07, 1] }}
                transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
              >
                <span
                  aria-hidden
                  className="absolute left-1/2 top-1/2 -z-10 size-16 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent/20 blur-xl"
                />
                <Logo className="h-10 w-auto" />
              </motion.span>

              <div className="w-full space-y-3" role="status" aria-live="polite">
                {STAGES.map((label, i) => {
                  const done = stage > i;
                  const active = stage === i;
                  return (
                    <div key={label} className="flex items-center gap-2.5 text-sm">
                      {done ? (
                        <Check className="size-4 shrink-0 text-accent" />
                      ) : active ? (
                        <Loader2 className="size-4 shrink-0 animate-spin text-accent" />
                      ) : (
                        <span className="grid size-4 shrink-0 place-items-center">
                          <span className="size-1.5 rounded-full bg-border-strong" />
                        </span>
                      )}
                      <span
                        className={cn(
                          "transition-colors duration-300",
                          done || active ? "text-text" : "text-faint",
                        )}
                      >
                        {label}
                      </span>
                    </div>
                  );
                })}
              </div>

              <div className="h-1 w-full overflow-hidden rounded-full bg-black/[0.07]">
                <div
                  className="h-full rounded-full bg-accent transition-all duration-500 ease-out"
                  style={{ width: `${(Math.min(stage, STAGES.length) / STAGES.length) * 100}%` }}
                />
              </div>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
