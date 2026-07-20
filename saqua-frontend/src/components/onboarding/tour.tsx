"use client";

import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";

const STORAGE_KEY = "saqua_onboarded_v1";

type Step = { sel: string; title: string; body: string; place: "right" | "top" | "bottom" };

const STEPS: Step[] = [
  {
    sel: '[data-tour="rail"]',
    title: "Threads & campaigns",
    body: "Your research threads and running campaigns live here — switch between them any time.",
    place: "right",
  },
  {
    sel: '[data-tour="composer"]',
    title: "Ask Saqua",
    body: "Describe who to find, or paste a list of companies. It researches, scores fit, and drafts the opener. Nothing sends without you.",
    place: "top",
  },
  {
    sel: '[data-tour="artifact-hint"]',
    title: "Drafts open beside the chat",
    body: "When Saqua writes an email, it opens in a panel here so you can read the full draft and copy it.",
    place: "right",
  },
  {
    sel: '[data-tour="nav-campaigns"]',
    title: "Track live sequences",
    body: "Campaigns shows every running sequence. The moment a prospect replies, the rest of the sequence stops automatically.",
    place: "right",
  },
];

type Rect = { top: number; left: number; width: number; height: number };

/** First-run guided walkthrough. Self-gates via localStorage so it never reappears
 * once completed or skipped. Points a tooltip at real interface elements marked
 * with data-tour. Functional (not decorative), so it renders even under
 * prefers-reduced-motion — just without the entrance transition. */
export function OnboardingTour() {
  const [active, setActive] = useState(false);
  const [step, setStep] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);

  // Decide on mount whether to run (first-time users only).
  useEffect(() => {
    let done = true;
    try {
      done = localStorage.getItem(STORAGE_KEY) === "1";
    } catch {
      /* storage blocked — treat as returning user, don't nag */
    }
    if (!done) {
      const t = setTimeout(() => setActive(true), 550); // let the layout settle first
      return () => clearTimeout(t);
    }
  }, []);

  const finish = useCallback(() => {
    try {
      localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      /* ignore */
    }
    setActive(false);
  }, []);

  // Measure the current step's target; skip any that aren't visible (e.g. rails
  // hidden on small screens). If none remain, finish.
  useEffect(() => {
    if (!active) return;
    let i = step;
    let el: HTMLElement | null = null;
    while (i < STEPS.length) {
      const found = document.querySelector<HTMLElement>(STEPS[i].sel);
      const r = found?.getBoundingClientRect();
      if (found && r && r.width > 0 && r.height > 0) {
        el = found;
        break;
      }
      i += 1;
    }
    if (!el) {
      finish();
      return;
    }
    if (i !== step) {
      setStep(i);
      return;
    }
    const measure = () => {
      const r = el!.getBoundingClientRect();
      setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
    };
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [active, step, finish]);

  if (!active || !rect) return null;

  const s = STEPS[step];
  const pad = 8;
  // Tooltip placement relative to the highlighted target, clamped to the viewport.
  const tipW = 300;
  let tipTop = rect.top;
  let tipLeft = rect.left;
  if (s.place === "right") {
    tipLeft = rect.left + rect.width + pad + 4;
    tipTop = Math.max(16, rect.top);
  } else if (s.place === "top") {
    tipLeft = rect.left + rect.width / 2 - tipW / 2;
    tipTop = rect.top - pad - 150;
  } else {
    tipLeft = rect.left;
    tipTop = rect.top + rect.height + pad + 4;
  }
  tipLeft = Math.min(Math.max(12, tipLeft), window.innerWidth - tipW - 12);
  tipTop = Math.min(Math.max(12, tipTop), window.innerHeight - 170);

  const last = step === STEPS.length - 1;

  return (
    <div className="fixed inset-0 z-[100]">
      {/* Spotlight: a ring at the target that dims everything else via box-shadow. */}
      <div
        className="pointer-events-none absolute rounded-lg ring-2 ring-accent transition-all duration-300 ease-smooth"
        style={{
          top: rect.top - pad,
          left: rect.left - pad,
          width: rect.width + pad * 2,
          height: rect.height + pad * 2,
          boxShadow: "0 0 0 9999px rgba(4,5,7,0.66)",
        }}
      />
      {/* Tooltip */}
      <div
        className="glass-panel animate-fade-up absolute w-[300px] rounded-xl border border-border p-4 shadow-pop"
        style={{ top: tipTop, left: tipLeft }}
      >
        <div className="mb-1 flex items-center justify-between">
          <span className="font-mono text-[11px] text-muted">
            {step + 1} of {STEPS.length}
          </span>
          <button
            onClick={finish}
            aria-label="Skip walkthrough"
            className="grid size-6 place-items-center rounded text-muted hover:bg-black/[0.05] hover:text-text"
          >
            <X className="size-3.5" />
          </button>
        </div>
        <div className="text-sm font-semibold text-text">{s.title}</div>
        <p className="mt-1.5 text-xs leading-5 text-text-2">{s.body}</p>
        <div className="mt-4 flex items-center justify-between">
          <button onClick={finish} className="text-xs text-muted hover:text-text-2">
            Skip
          </button>
          <div className="flex items-center gap-2">
            {step > 0 && (
              <button
                onClick={() => setStep((v) => v - 1)}
                className="rounded-md px-2.5 py-1.5 text-xs text-text-2 hover:bg-black/[0.05] hover:text-text"
              >
                Back
              </button>
            )}
            <button
              onClick={() => (last ? finish() : setStep((v) => v + 1))}
              className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-[color:var(--accent-ink)] hover:bg-accent-hi"
            >
              {last ? "Got it" : "Next"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
