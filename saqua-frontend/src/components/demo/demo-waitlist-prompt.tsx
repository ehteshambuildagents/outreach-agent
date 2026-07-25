"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { BadgePercent, Check, X } from "lucide-react";
import { useDemo } from "@/components/demo/demo-provider";

/**
 * The one moment we ask a demo visitor for something, and only after they have
 * shown real interest: two agent messages sent, or a few minutes spent inside
 * the workspace. Never on arrival, and never twice in a session.
 *
 * On the ask itself: every demo visitor reached the workspace through the Gmail
 * gate, and that gate adds their address to the real waitlist before it mints
 * the session (``waitlist.join(email, source="demo")`` in ``server/demo_api``).
 * So by construction they are ALREADY on the list, and the honest thing to show
 * is the reservation, not a second email field. That is why there is no form
 * here: we never ask for an address we already hold.
 */

const DISMISS_KEY = "saqua_demo_prompt_done";
const START_KEY = "saqua_demo_started_at";
// "About 3 to 4 minutes" of dwell, or two real messages, whichever lands first.
const DWELL_MS = 3.5 * 60 * 1000;
const TURNS_TRIGGER = 2;

function readStart(): number {
  try {
    const raw = window.sessionStorage.getItem(START_KEY);
    if (raw) {
      const n = Number.parseInt(raw, 10);
      if (Number.isFinite(n)) return n;
    }
    const now = Date.now();
    window.sessionStorage.setItem(START_KEY, String(now));
    return now;
  } catch {
    return Date.now(); // storage blocked: fall back to this mount
  }
}

export function DemoWaitlistPrompt() {
  const { isDemo, turnsUsed } = useDemo();
  const [open, setOpen] = useState(false);
  const dismissed = useRef(false);
  const startedAt = useRef<number | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    try {
      dismissed.current = window.sessionStorage.getItem(DISMISS_KEY) === "1";
    } catch {
      /* storage blocked: the in-memory ref still stops a repeat this session */
    }
  }, []);

  const close = useCallback(() => {
    dismissed.current = true;
    setOpen(false);
    try {
      window.sessionStorage.setItem(DISMISS_KEY, "1");
    } catch {
      /* ignore */
    }
  }, []);

  // Engagement watch. Cheap interval rather than a timeout so the turn-based
  // trigger and the dwell trigger share one code path, and so a visitor who
  // arrives mid-session is still measured from their first sight of the app.
  useEffect(() => {
    if (!isDemo || dismissed.current) return;
    if (startedAt.current === null) startedAt.current = readStart();

    const evaluate = () => {
      if (dismissed.current) return;
      const dwelled = Date.now() - (startedAt.current ?? Date.now()) >= DWELL_MS;
      if (turnsUsed >= TURNS_TRIGGER || dwelled) setOpen(true);
    };
    evaluate();
    const id = window.setInterval(evaluate, 15_000);
    return () => window.clearInterval(id);
  }, [isDemo, turnsUsed]);

  // Escape closes, and the dialog takes focus so a keyboard user is not stranded.
  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  if (!isDemo || !open) return null;

  return createPortal(
    <div className="fixed inset-0 z-[110] grid place-items-center px-5">
      <button
        type="button"
        aria-label="Close"
        onClick={close}
        className="absolute inset-0 cursor-default bg-black/25 backdrop-blur-[2px]"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="demo-prompt-title"
        className="demo-entry-content relative w-full max-w-sm rounded-2xl border border-border bg-card p-7 text-center shadow-pop"
      >
        <button
          type="button"
          onClick={close}
          aria-label="Dismiss"
          className="absolute right-3 top-3 grid size-7 place-items-center rounded-md text-muted transition-colors hover:bg-black/[0.05] hover:text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <X className="size-4" />
        </button>

        <div className="mx-auto grid size-11 place-items-center rounded-full bg-accent-soft text-accent">
          <BadgePercent className="size-5" />
        </div>

        <h2 id="demo-prompt-title" className="mt-4 font-display text-xl font-semibold text-text">
          Enjoying Saqua?
        </h2>

        <p className="mt-2.5 flex items-start justify-center gap-1.5 text-sm leading-6 text-text-2">
          <Check className="mt-1 size-3.5 shrink-0 text-[color:var(--success)]" />
          <span>
            You&apos;re already on the waitlist. Your 40% lifetime discount has been reserved.
          </span>
        </p>

        <button
          ref={closeRef}
          type="button"
          onClick={close}
          className="mt-6 inline-flex h-11 w-full items-center justify-center rounded-lg border border-border-strong bg-white px-5 text-sm font-semibold text-text transition-all hover:-translate-y-px hover:border-accent-line hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Continue exploring
        </button>
      </div>
    </div>,
    document.body,
  );
}
