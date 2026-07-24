"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Loader2, Lock, Sparkles } from "lucide-react";
import { HeroForm } from "@/components/marketing/hero-form";
import { cn } from "@/lib/utils";

/**
 * The entry gate to the sandboxed in-app demo.
 *
 * A visitor gives a work email (no account, no password). That mints a signed,
 * short-lived demo session cookie via ``POST /api/demo/session`` — passing the
 * SAME abuse gate as a live run (honeypot, per-IP + per-email caps, global daily
 * budget, soft waitlist add). On success we navigate into the REAL app (``/ai``),
 * where the middleware now recognises the demo cookie and every page renders
 * over demo-scoped data.
 *
 * Every backend block (capacity, per-IP/email limit) comes back as a clean
 * ``{state,message}`` JSON, rendered here as an honest message with the waitlist —
 * never a broken error.
 */

type Blocked = { state: string; title: string; message: string };

export function DemoGate() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState(""); // honeypot
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState<Blocked | null>(null);

  const canStart = /.+@.+\..+/.test(email.trim());

  async function start(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !canStart) return;
    setBusy(true);
    setBlocked(null);

    let res: Response;
    try {
      res = await fetch("/api/demo/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ email, company }),
      });
    } catch {
      setBlocked({
        state: "error",
        title: "Couldn't reach the demo",
        message: "Check your connection and try again.",
      });
      setBusy(false);
      return;
    }

    let payload: Record<string, unknown> = {};
    try {
      payload = await res.json();
    } catch {
      /* fall through */
    }

    if (res.ok && payload.active) {
      // Cookie is set; enter the real product. A full navigation ensures the
      // middleware sees the new cookie and the shell boots in demo mode.
      window.location.assign("/ai");
      return;
    }

    const state = (payload.state as string) || "error";
    setBlocked({
      state,
      title:
        state === "capacity"
          ? "Today's demo capacity is full"
          : state === "rate_limited"
            ? "That's all for now"
            : state === "need_email"
              ? "That email didn't look right"
              : "The demo is unavailable",
      message:
        (payload.message as string) ||
        "The demo is unavailable right now. Please try again shortly.",
    });
    setBusy(false);
  }

  const showWaitlist = blocked && ["capacity", "rate_limited"].includes(blocked.state);

  return (
    <div className="mx-auto w-full max-w-lg">
      <form onSubmit={start} className="glass rounded-2xl border border-border p-6 shadow-card md:p-8">
        <div className="mb-5 flex items-center gap-2 text-sm font-semibold text-accent">
          <Sparkles className="size-4" /> Start your live demo
        </div>
        <p className="mb-5 text-sm leading-6 text-muted">
          You&apos;ll step into the real Saqua workspace — chat, prospects, settings — running the
          real research pipeline on live data. No account, just a work email so we can hold your
          spot. Sending stays off until Gmail clears Google&apos;s review.
        </p>

        {/* Honeypot — hidden; a bot that fills it gets a benign non-start. */}
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

        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Your work email"
          aria-label="Work email"
          disabled={busy}
          className="h-12 w-full rounded-lg border border-border-strong bg-white px-4 text-sm text-text outline-none transition-all placeholder:text-faint focus:border-accent-line focus:shadow-[0_0_0_4px_var(--accent-soft)] disabled:opacity-60"
        />

        <button
          type="submit"
          disabled={busy || !canStart}
          className={cn(
            "mt-3 inline-flex h-12 w-full items-center justify-center gap-2 rounded-lg bg-accent text-sm font-semibold text-white shadow-[0_1px_2px_rgba(79,90,247,.35)] transition-all hover:-translate-y-px hover:bg-accent-hi hover:shadow-[0_8px_22px_rgba(79,90,247,.32)]",
            "disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0",
          )}
        >
          {busy ? (
            <>
              <Loader2 className="size-4 animate-spin" /> Setting up your demo…
            </>
          ) : (
            <>
              Enter the live demo <ArrowRight className="size-4" />
            </>
          )}
        </button>

        <p className="mt-3 flex items-center justify-center gap-1.5 text-center text-xs text-muted">
          <Lock className="size-3" /> No account or password. Real pipeline, drafts only.
        </p>
      </form>

      {blocked && (
        <div className="mt-6">
          <div className="surface rounded-2xl p-6 text-center shadow-card">
            <p className="text-base font-medium text-text">{blocked.title}</p>
            <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted">{blocked.message}</p>
          </div>
          {showWaitlist && (
            <div className="mt-6">
              <HeroForm source="demo_gate_blocked" label="Join the waitlist" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
