"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  Loader2,
  Lock,
  Quote,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Target,
  UserRound,
  X,
} from "lucide-react";
import { HeroForm } from "@/components/marketing/hero-form";
import { cn } from "@/lib/utils";

/**
 * The public, no-login LIVE DEMO.
 *
 * It POSTs to /api/demo/run and reads a Server-Sent Events stream, rendering each
 * real pipeline stage as it lands: ICP → discovery → per-candidate research +
 * qualification (with fit scores) → the best-fit's real signal + a real drafted
 * opener → the Gmail-pending message + the waitlist form.
 *
 * Every backend BLOCK (daily capacity, per-IP/email limit, in-progress) arrives as
 * a normal JSON body with a {state,message}; only a live run is text/event-stream.
 * So one content-type check decides whether we stream or render a clean state.
 *
 * No result is mocked — an empty backend (no providers) surfaces an honest "empty"
 * state, never a fake company.
 */

type Signal = { text: string | null; quote?: string | null; source_url?: string | null };
type Person = { name?: string | null; role?: string | null };
type Candidate = {
  index: number;
  company: string;
  domain: string;
  website?: string;
  why_discovered?: string;
  fit_score?: number | null;
  fit_level?: string;
  recommendation?: string;
  why?: string;
  signal?: Signal;
  person?: Person;
  researching?: boolean;
  timed_out?: boolean;
};
type GuardVerdict = { decision?: string; risk?: number };
type Draft = {
  company?: string;
  subject?: string;
  body?: string;
  to?: string | null;
  guard?: GuardVerdict;
};
type Blocked = { state: string; message: string };

type Phase = "form" | "running" | "done" | "blocked";
type Mode = "icp" | "website";

const STAGE_FALLBACK = "Working…";

/** Example ICPs a first-time visitor can run in one click. Each one has been
 * verified against the real pipeline (2026-07-24): it discovers real companies
 * and at least one clears the pursue bar, so the suggested demo never showcases
 * an all-reject run. Keep these concrete — company-type ICPs match far better
 * than abstract stage-based ones ("seed-stage startups" pulls directories). */
const EXAMPLE_PROMPTS = [
  "digital marketing agencies serving e-commerce brands",
  "recruiting agencies that place software engineers",
  "managed IT service providers for law firms",
];

export function DemoConsole() {
  const [mode, setMode] = useState<Mode>("icp");
  const [icp, setIcp] = useState("");
  const [website, setWebsite] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState(""); // honeypot

  const [phase, setPhase] = useState<Phase>("form");
  const [stage, setStage] = useState("");
  const [icpLabel, setIcpLabel] = useState("");
  const [keywords, setKeywords] = useState<string[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [topDomain, setTopDomain] = useState<string | null>(null);
  const [top, setTop] = useState<Candidate | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [draftSkip, setDraftSkip] = useState<string | null>(null);
  const [noPursue, setNoPursue] = useState<string | null>(null);
  const [gmailMsg, setGmailMsg] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<Blocked | null>(null);

  const resultsRef = useRef<HTMLDivElement>(null);

  const reset = () => {
    setStage("");
    setIcpLabel("");
    setKeywords([]);
    setCandidates([]);
    setTopDomain(null);
    setTop(null);
    setDraft(null);
    setDraftSkip(null);
    setNoPursue(null);
    setGmailMsg(null);
    setBlocked(null);
  };

  const handleEvent = useCallback((event: string, data: Record<string, unknown>) => {
    switch (event) {
      case "stage":
        setStage((data.label as string) || STAGE_FALLBACK);
        break;
      case "icp":
        setIcpLabel((data.label as string) || "");
        setKeywords((data.keywords as string[]) || []);
        break;
      case "candidates":
        setStage("Researching and scoring each match");
        setCandidates(
          ((data.prospects as Candidate[]) || []).map((p) => ({ ...p, researching: true })),
        );
        break;
      case "scored":
        setCandidates((prev) =>
          prev.map((c) =>
            c.index === (data.index as number)
              ? { ...c, ...(data as Partial<Candidate>), researching: false }
              : c,
          ),
        );
        break;
      case "top":
        setTopDomain((data.domain as string) || null);
        setTop(data as Candidate);
        setStage("Writing the opener around the evidence");
        break;
      case "draft":
        setDraft(data as Draft);
        break;
      case "draft_skip":
        setDraftSkip((data.reason as string) || "");
        break;
      case "no_pursue":
        // Honest terminal state: real scores are on the cards, but nothing cleared
        // the bar, so there is no crowned best fit and no draft.
        setNoPursue((data.reason as string) || "None of these cleared the pursue bar.");
        setStage("");
        break;
      case "gmail_pending":
        setGmailMsg((data.message as string) || "");
        break;
      case "empty":
        setBlocked({ state: "empty", message: (data.reason as string) || "No matches found." });
        setPhase("blocked");
        break;
      case "error":
        setBlocked({
          state: "error",
          message: (data.reason as string) || "Something went wrong. Please try again.",
        });
        setPhase("blocked");
        break;
      case "done":
        setStage("");
        setPhase("done");
        break;
      default:
        break;
    }
  }, []);

  /** Start a run. With `icpOverride` (an example chip) the ICP field is filled and
   * the run starts immediately when the email gate is already satisfied; otherwise
   * the chip only fills the field so the visitor can add their email first. */
  async function startRun(icpOverride?: string) {
    if (phase === "running") return;
    const icpText = icpOverride ?? (mode === "icp" ? icp : "");
    if (icpOverride !== undefined) {
      setMode("icp");
      setIcp(icpOverride);
      if (email.trim().length <= 3) return; // filled, not run — email gate first
    }
    reset();
    setPhase("running");
    setStage("Starting");

    let res: Response;
    try {
      res = await fetch("/api/demo/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          email,
          icp: icpOverride !== undefined ? icpText : mode === "icp" ? icp : "",
          website: icpOverride !== undefined ? "" : mode === "website" ? website : "",
          company,
        }),
      });
    } catch {
      setBlocked({ state: "error", message: "Couldn't reach the demo. Check your connection and try again." });
      setPhase("blocked");
      return;
    }

    const ctype = res.headers.get("content-type") || "";
    if (!ctype.includes("text/event-stream")) {
      let payload: Record<string, unknown> = {};
      try {
        payload = await res.json();
      } catch {
        /* fall through to a generic message */
      }
      setBlocked({
        state: (payload.state as string) || "error",
        message:
          (payload.message as string) ||
          (payload.error as string) ||
          "The demo is unavailable right now. Please try again shortly.",
      });
      setPhase("blocked");
      return;
    }

    if (!res.body) {
      setBlocked({ state: "error", message: "The demo stream didn't open. Please try again." });
      setPhase("blocked");
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sep: number;
        while ((sep = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          let event = "message";
          const dataLines: string[] = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          if (!dataLines.length) continue;
          try {
            handleEvent(event, JSON.parse(dataLines.join("\n")));
          } catch {
            /* ignore a malformed frame rather than break the whole stream */
          }
        }
      }
    } catch {
      setBlocked({ state: "error", message: "The demo stream was interrupted. Please try again." });
      setPhase("blocked");
      return;
    }
    // Stream closed cleanly without an explicit terminal event.
    setPhase((p) => (p === "running" ? "done" : p));
  }

  // Bring the results into view once a run starts.
  useEffect(() => {
    if (phase === "running" && resultsRef.current) {
      resultsRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [phase]);

  const canSubmit =
    email.trim().length > 3 &&
    (mode === "icp" ? icp.trim().length > 2 : website.trim().length > 2);

  const showWaitlistOnBlock =
    blocked && ["capacity", "rate_limited", "in_progress", "empty"].includes(blocked.state);

  return (
    <div className="mx-auto w-full max-w-4xl">
      {/* ── Input ─────────────────────────────────────────────────────── */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void startRun();
        }}
        className="glass mx-auto max-w-xl rounded-2xl border border-border p-6 shadow-card"
      >
        <div className="mb-4 inline-flex rounded-full border border-border bg-white/60 p-1 text-sm">
          {(["icp", "website"] as Mode[]).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={cn(
                "rounded-full px-4 py-1.5 font-medium transition-all",
                mode === m ? "bg-accent text-white shadow-sm" : "text-muted hover:text-text",
              )}
            >
              {m === "icp" ? "Describe who you sell to" : "Use my website"}
            </button>
          ))}
        </div>

        {/* Honeypot */}
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

        {mode === "icp" ? (
          <>
            <textarea
              value={icp}
              onChange={(e) => setIcp(e.target.value)}
              maxLength={200}
              rows={2}
              placeholder={`e.g. ${EXAMPLE_PROMPTS[0]}`}
              aria-label="Who you sell to"
              disabled={phase === "running"}
              className="w-full resize-none rounded-lg border border-border-strong bg-white px-4 py-3 text-sm text-text outline-none transition-all placeholder:text-faint focus:border-accent-line focus:shadow-[0_0_0_4px_var(--accent-soft)] disabled:opacity-60"
            />
            <div className="mt-2 flex flex-wrap gap-1.5">
              {EXAMPLE_PROMPTS.map((p) => (
                <button
                  key={p}
                  type="button"
                  disabled={phase === "running"}
                  onClick={() => void startRun(p)}
                  className="rounded-full border border-border bg-white/70 px-3 py-1 text-xs text-muted transition-colors hover:border-accent-line hover:text-accent disabled:opacity-50"
                >
                  {p}
                </button>
              ))}
            </div>
          </>
        ) : (
          <input
            type="text"
            inputMode="url"
            value={website}
            onChange={(e) => setWebsite(e.target.value)}
            maxLength={300}
            placeholder="yourcompany.com — we'll infer who you sell to"
            aria-label="Your website"
            disabled={phase === "running"}
            className="h-12 w-full rounded-lg border border-border-strong bg-white px-4 text-sm text-text outline-none transition-all placeholder:text-faint focus:border-accent-line focus:shadow-[0_0_0_4px_var(--accent-soft)] disabled:opacity-60"
          />
        )}

        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Your work email"
          aria-label="Work email"
          disabled={phase === "running"}
          className="mt-3 h-12 w-full rounded-lg border border-border-strong bg-white px-4 text-sm text-text outline-none transition-all placeholder:text-faint focus:border-accent-line focus:shadow-[0_0_0_4px_var(--accent-soft)] disabled:opacity-60"
        />

        <button
          type="submit"
          disabled={phase === "running" || !canSubmit}
          className="mt-3 inline-flex h-12 w-full items-center justify-center gap-2 rounded-lg bg-accent text-sm font-semibold text-white shadow-[0_1px_2px_rgba(79,90,247,.35)] transition-all hover:-translate-y-px hover:bg-accent-hi hover:shadow-[0_8px_22px_rgba(79,90,247,.32)] disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0"
        >
          {phase === "running" ? (
            <>
              <Loader2 className="size-4 animate-spin" /> Running the pipeline
            </>
          ) : (
            <>
              Run the live demo <ArrowRight className="size-4" />
            </>
          )}
        </button>
        <p className="mt-3 flex items-center justify-center gap-1.5 text-center text-xs text-muted">
          <Lock className="size-3" /> No account needed. Runs the real research pipeline on live data.
        </p>
      </form>

      {/* ── Results / states ──────────────────────────────────────────── */}
      <div ref={resultsRef} className="scroll-mt-24">
        {phase === "blocked" && blocked && (
          <div className="mx-auto mt-8 max-w-xl">
            <div className="surface rounded-2xl p-6 text-center shadow-card">
              <div className="mx-auto mb-3 grid size-10 place-items-center rounded-full bg-accent-soft text-accent">
                {blocked.state === "error" ? <X className="size-5" /> : <Sparkles className="size-5" />}
              </div>
              <p className="text-base font-medium text-text">
                {blocked.state === "capacity"
                  ? "Today's demo capacity is full"
                  : blocked.state === "empty"
                    ? "No strong matches this time"
                    : blocked.state === "in_progress"
                      ? "A run is already going"
                      : blocked.state === "rate_limited"
                        ? "That's all for now"
                        : "Something went wrong"}
              </p>
              <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted">{blocked.message}</p>
            </div>
            {showWaitlistOnBlock && (
              <div className="mt-6">
                <HeroForm source="demo_blocked" label="Join the waitlist" />
              </div>
            )}
          </div>
        )}

        {(phase === "running" || phase === "done") && (
          <div className="mt-8 space-y-6">
            {/* Live status strip */}
            {phase === "running" && stage && (
              <div className="mx-auto flex max-w-xl items-center justify-center gap-2 text-sm text-muted">
                <Loader2 className="size-4 animate-spin text-accent" /> {stage}
                <span className="inline-flex gap-0.5" aria-hidden>
                  <span className="size-1 animate-pulse rounded-full bg-accent [animation-delay:0ms]" />
                  <span className="size-1 animate-pulse rounded-full bg-accent [animation-delay:150ms]" />
                  <span className="size-1 animate-pulse rounded-full bg-accent [animation-delay:300ms]" />
                </span>
              </div>
            )}

            {icpLabel && (
              <div className="flex flex-wrap items-center justify-center gap-2 text-sm">
                <Target className="size-4 text-accent" />
                <span className="text-muted">Finding matches for</span>
                <span className="font-medium text-text">{icpLabel}</span>
                {keywords.slice(0, 5).map((k) => (
                  <span key={k} className="rounded-full bg-accent-soft px-2.5 py-0.5 text-xs font-medium text-accent">
                    {k}
                  </span>
                ))}
              </div>
            )}

            {/* Candidate grid */}
            {candidates.length > 0 && (
              <div>
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-text">
                  <Search className="size-4 text-accent" /> Candidates, scored by fit
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  {candidates.map((c) => (
                    <CandidateCard key={c.index} c={c} isTop={c.domain === topDomain} />
                  ))}
                </div>
              </div>
            )}

            {/* Honest no-pursue terminal state: scores stay on the cards above,
                but nothing cleared the bar — so no crown, no draft. Offer verified
                one-click ICPs (the email gate is already satisfied here). */}
            {noPursue && (
              <div className="surface rounded-2xl p-6 shadow-card motion-safe:animate-fade-up">
                <div className="flex items-center gap-2 text-sm font-semibold text-text">
                  <Target className="size-4 text-accent" /> Nothing cleared the bar — so Saqua won&apos;t write
                </div>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-muted">{noPursue}</p>
                <div className="mt-4 flex flex-wrap items-center gap-1.5">
                  <span className="text-xs text-muted">Try one that matches well:</span>
                  {EXAMPLE_PROMPTS.map((p) => (
                    <button
                      key={p}
                      type="button"
                      disabled={phase === "running"}
                      onClick={() => void startRun(p)}
                      className="rounded-full border border-border bg-white/70 px-3 py-1 text-xs text-muted transition-colors hover:border-accent-line hover:text-accent disabled:opacity-50"
                    >
                      {p}
                    </button>
                  ))}
                </div>
                <div className="mt-6">
                  <HeroForm source="demo_no_pursue" label="Join the waitlist" />
                </div>
              </div>
            )}

            {/* Top match: signal + drafted opener */}
            {top && (
              <div className="surface overflow-hidden rounded-2xl shadow-card motion-safe:animate-fade-up">
                <div className="border-b border-border-faint bg-accent-soft/40 px-6 py-4">
                  <div className="flex items-center gap-2 text-sm font-semibold text-accent">
                    <Sparkles className="size-4" /> Best fit — {top.company}
                  </div>
                  {top.person?.name && (
                    <div className="mt-1 flex items-center gap-1.5 text-xs text-text-2">
                      <UserRound className="size-3.5 text-accent" />
                      {top.person.name}
                      {top.person.role ? ` — ${top.person.role}` : ""}
                      <span className="text-muted">· found by research, with the source below</span>
                    </div>
                  )}
                </div>
                <div className="space-y-5 p-6">
                  {top.signal?.text && (
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-wide text-muted">The signal it found</div>
                      <p className="mt-1.5 text-sm leading-6 text-text-2">{top.signal.text}</p>
                      {top.signal.quote && (
                        <blockquote className="mt-2 flex gap-2 border-l-2 border-accent-line pl-3 text-sm italic leading-6 text-muted">
                          <Quote className="mt-0.5 size-3.5 shrink-0 text-accent" />
                          <span>{top.signal.quote}</span>
                        </blockquote>
                      )}
                      {top.signal.source_url && (
                        <a
                          href={top.signal.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="mt-1.5 inline-block text-xs text-accent hover:underline"
                        >
                          {sourceLabel(top.signal.source_url)}
                        </a>
                      )}
                    </div>
                  )}

                  {draft ? (
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <div className="text-xs font-semibold uppercase tracking-wide text-muted">The opener it wrote</div>
                        <GuardChip guard={draft.guard} />
                      </div>
                      <div className="mt-2 rounded-xl border border-border-faint bg-card-2 p-5">
                        {draft.subject && (
                          <div className="border-b border-border-faint pb-2 font-[family-name:var(--font-serif)] text-base font-medium text-text">
                            {draft.subject}
                          </div>
                        )}
                        <p className="whitespace-pre-wrap pt-3 font-[family-name:var(--font-serif)] text-[15px] leading-7 text-text-2">
                          {draft.body}
                        </p>
                      </div>
                    </div>
                  ) : draftSkip ? (
                    <div className="rounded-xl border border-border-faint bg-card-2 p-4 text-sm leading-6 text-muted">
                      {draftSkip}
                    </div>
                  ) : (
                    phase === "running" && (
                      <div className="flex items-center gap-2 text-sm text-muted">
                        <Loader2 className="size-4 animate-spin text-accent" /> Writing the opener…
                      </div>
                    )
                  )}
                </div>
              </div>
            )}

            {/* Gmail pending + waitlist */}
            {gmailMsg && (
              <div className="rounded-2xl border border-accent-line bg-accent-soft/50 p-6 text-center">
                <div className="mx-auto mb-3 grid size-10 place-items-center rounded-full bg-white text-accent shadow-sm">
                  <Lock className="size-5" />
                </div>
                <p className="mx-auto max-w-lg text-sm leading-6 text-text-2">{gmailMsg}</p>
                <div className="mt-5">
                  <HeroForm source="demo_done" label="Join the waitlist" />
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Verdict of the production Deliverability & Cost Guard, run on the finished
 * draft — shown only when the backend actually ran it (never assumed). */
function GuardChip({ guard }: { guard?: GuardVerdict }) {
  if (!guard?.decision) return null;
  if (guard.decision === "ALLOW") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2.5 py-0.5 text-[11px] font-medium text-accent">
        <ShieldCheck className="size-3" /> Guard: passed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-warn-soft px-2.5 py-0.5 text-[11px] font-medium text-[color:var(--warn)]">
      <ShieldAlert className="size-3" /> Guard: flagged notes
    </span>
  );
}

function sourceLabel(url: string): string {
  try {
    return "Source: " + new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "Source";
  }
}

/** Fit-level badge derived from the SAME score shown on the card, so the badge, the
 * number, and the Pursue/Skip recommendation never contradict each other. The
 * weak↔moderate cut sits at the Pursue threshold (45) on purpose. */
function scoreFitLevel(score: number): string {
  if (score >= 65) return "strong";
  if (score >= 45) return "moderate";
  return "weak";
}

function CandidateCard({ c, isTop }: { c: Candidate; isTop: boolean }) {
  const score = useCountUp(typeof c.fit_score === "number" ? c.fit_score : null);
  const level = typeof c.fit_score === "number" ? scoreFitLevel(c.fit_score) : "";
  return (
    <div
      className={cn(
        "surface relative flex flex-col rounded-2xl p-5 transition-all motion-safe:animate-fade-up",
        isTop && "border-accent-line shadow-[0_0_0_1px_var(--accent-line),0_18px_44px_-22px_rgba(79,90,247,.4)]",
      )}
    >
      {isTop && (
        <span className="absolute -top-2 right-4 rounded-full bg-accent px-2.5 py-0.5 text-[11px] font-semibold text-white shadow-sm">
          Best fit
        </span>
      )}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-display text-lg font-semibold text-text">{c.company}</div>
          <div className="truncate text-xs text-muted">{c.domain}</div>
          {c.person?.name && (
            <div className="mt-0.5 flex items-center gap-1 truncate text-xs text-text-2">
              <UserRound className="size-3 shrink-0 text-accent" />
              <span className="truncate">
                {c.person.name}
                {c.person.role ? ` — ${c.person.role}` : ""}
              </span>
            </div>
          )}
        </div>
        <div className="shrink-0 text-right">
          {c.researching ? (
            <Loader2 className="size-5 animate-spin text-accent" />
          ) : c.timed_out ? (
            <span className="text-xs text-faint">—</span>
          ) : (
            <div>
              <div className="font-mono text-2xl font-semibold leading-none text-accent">{score ?? "—"}</div>
              <div className="text-[10px] uppercase tracking-wide text-muted">/ 100</div>
            </div>
          )}
        </div>
      </div>

      {/* Fit bar */}
      {!c.researching && !c.timed_out && typeof c.fit_score === "number" && (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-black/[0.06]">
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-700 ease-smooth"
            style={{ width: `${Math.max(4, Math.min(100, c.fit_score))}%` }}
          />
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {level && <FitBadge level={level} />}
        {c.recommendation && <RecoBadge reco={c.recommendation} />}
      </div>

      <p className="mt-3 line-clamp-2 text-sm leading-6 text-text-2">
        {c.researching
          ? "Researching the site for who they are and any recent buying signal…"
          : c.timed_out
            ? "Ran out of time this run."
            : c.why || c.why_discovered}
      </p>

      {!c.researching && c.signal?.text && (
        <p className="mt-2 line-clamp-2 border-l-2 border-border pl-2.5 text-xs leading-5 text-muted">
          {c.signal.text}
        </p>
      )}
    </div>
  );
}

function FitBadge({ level }: { level: string }) {
  const map: Record<string, string> = {
    strong: "bg-accent-soft text-accent",
    moderate: "bg-accent-soft/60 text-accent",
    weak: "bg-black/[0.05] text-muted",
    unknown: "bg-black/[0.05] text-muted",
  };
  return (
    <span className={cn("rounded-full px-2.5 py-0.5 text-xs font-medium capitalize", map[level] || map.unknown)}>
      {level} fit
    </span>
  );
}

function RecoBadge({ reco }: { reco: string }) {
  const label: Record<string, string> = {
    high_priority: "High priority",
    continue: "Pursue",
    research_more: "Needs more",
    reject: "Skip",
  };
  const style: Record<string, string> = {
    high_priority: "bg-accent text-white",
    continue: "bg-accent-soft text-accent",
    research_more: "bg-warn-soft text-[color:var(--warn)]",
    reject: "bg-black/[0.05] text-muted",
  };
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium", style[reco] || style.reject)}>
      {reco === "reject" ? <X className="size-3" /> : <Check className="size-3" />}
      {label[reco] || reco}
    </span>
  );
}

/** Count a number up from 0 once, respecting prefers-reduced-motion. The final
 * value is guaranteed even if rAF never fires (throttled/paused in a background
 * tab) via a timeout fallback — the animation is only a flourish, never the source
 * of truth for the score. */
function useCountUp(target: number | null): number | null {
  const [value, setValue] = useState<number | null>(null);
  useEffect(() => {
    if (target === null) {
      setValue(null);
      return;
    }
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setValue(target);
      return;
    }
    let raf = 0;
    const start = performance.now();
    const dur = 600;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / dur);
      setValue(Math.round(target * (1 - Math.pow(1 - t, 3))));
      if (t < 1) raf = requestAnimationFrame(tick);
      else setValue(target);
    };
    raf = requestAnimationFrame(tick);
    // rAF is paused in hidden/background tabs; guarantee the number still lands.
    const settle = window.setTimeout(() => setValue(target), dur + 400);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(settle);
    };
  }, [target]);
  return value;
}
