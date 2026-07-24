"use client";

import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  ArrowUp,
  Check,
  Loader2,
  Lock,
  Quote,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  UserRound,
  X,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Logo } from "@/components/ui/logo";
import { HeroForm } from "@/components/marketing/hero-form";
import { cn } from "@/lib/utils";

/**
 * The public, no-login LIVE DEMO — presented as the product's chat experience.
 *
 * Structure mirrors the real dashboard assistant ((app)/ai): a conversational
 * composer, messages appearing in sequence, and results arriving as inline
 * cards in the flow — user bubble → assistant narration → candidates card whose
 * scores land live → best-fit card with the real signal + source link → the
 * guard-checked draft → the honest Gmail-pending note with the waitlist. It is
 * a separate lightweight component (no Clerk, no per-user conversation store),
 * but the visual language is the product's.
 *
 * It POSTs to /api/demo/run and reads the Server-Sent Events stream; every
 * backend BLOCK (capacity, per-IP/email limits, in-progress) arrives as normal
 * JSON with {state,message}, so one content-type check decides whether we
 * stream or render a clean assistant message. No result is ever mocked — an
 * empty backend surfaces an honest "empty" message, never a fake company.
 *
 * The email gate is conversational: the first request gets an assistant reply
 * asking for a work email inline; the run starts the moment it's provided.
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

type Msg =
  | { id: number; role: "user"; kind: "text"; text: string }
  | { id: number; role: "assistant"; kind: "text"; text: string }
  | { id: number; role: "assistant"; kind: "email_gate"; note?: string }
  | { id: number; role: "assistant"; kind: "candidates"; items: Candidate[] }
  | { id: number; role: "assistant"; kind: "top"; data: Candidate }
  | { id: number; role: "assistant"; kind: "draft"; data: Draft }
  | { id: number; role: "assistant"; kind: "suggest"; text: string; waitlist: boolean }
  | { id: number; role: "assistant"; kind: "gmail"; text: string }
  | { id: number; role: "assistant"; kind: "blocked"; title: string; text: string; waitlist: boolean };

/** Omit that distributes over the Msg union (plain Omit collapses a union to
 * its common keys, which breaks pushing kind-specific messages). */
type NewMsg = Msg extends infer M ? (M extends Msg ? Omit<M, "id"> : never) : never;

/** Example asks a first-time visitor can run in one click. Each one has been
 * verified against the real pipeline (2026-07-24): it discovers real companies
 * and at least one clears the pursue bar, so the suggested first run never
 * showcases an all-reject result. Keep these concrete — company-type ICPs match
 * far better than abstract stage-based ones ("seed-stage startups" pulls
 * directories). */
const EXAMPLE_PROMPTS = [
  "digital marketing agencies serving e-commerce brands",
  "recruiting agencies that place software engineers",
  "managed IT service providers for law firms",
];

const RUN_NOTE = "research runs live, so this can take a minute";

/** A single-token domain/URL message is treated as "use my website" — the
 * backend then infers who they sell to from their own site. */
function looksLikeWebsite(t: string): boolean {
  const s = t.trim();
  return !s.includes(" ") && /^(https?:\/\/)?[\w-]+(\.[a-z][a-z0-9-]{1,})+(\/\S*)?$/i.test(s);
}

/** Client-side plausibility only — the server (waitlist.valid) is the authority
 * and a server-side reject re-opens the gate with its message. */
function emailish(e: string): boolean {
  return /.+@.+\..+/.test(e.trim());
}

export function DemoConsole() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [email, setEmail] = useState("");
  const [emailDraft, setEmailDraft] = useState("");
  const [company, setCompany] = useState(""); // honeypot — hidden field, bots fill it
  const [running, setRunning] = useState(false);
  const [stage, setStage] = useState("");
  const [pendingAsk, setPendingAsk] = useState<string | null>(null);

  const idRef = useRef(1);
  const candMsgRef = useRef<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const push = (m: NewMsg): number => {
    const id = idRef.current++;
    setMessages((prev) => [...prev, { ...m, id } as Msg]);
    return id;
  };

  const patchCandidate = (msgId: number, index: number, patch: Partial<Candidate>) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === msgId && m.kind === "candidates"
          ? { ...m, items: m.items.map((c) => (c.index === index ? { ...c, ...patch } : c)) }
          : m,
      ),
    );
  };

  // Keep the newest message in view, like the real chat.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, stage, running]);

  function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || running) return;
    setInput("");
    push({ role: "user", kind: "text", text: trimmed });
    if (!emailish(email)) {
      // Conversational email gate: ask inline, run the moment it's provided.
      setPendingAsk(trimmed);
      push({ role: "assistant", kind: "email_gate" });
      return;
    }
    void run(trimmed);
  }

  function submitEmail(e: React.FormEvent) {
    e.preventDefault();
    const v = emailDraft.trim();
    if (!emailish(v) || running) return;
    setEmail(v);
    const ask = pendingAsk;
    setPendingAsk(null);
    if (ask) void run(ask, v);
  }

  function handleEvent(event: string, data: Record<string, unknown>) {
    switch (event) {
      case "stage":
        setStage((data.label as string) || "Working…");
        break;
      case "icp": {
        const label = (data.label as string) || "";
        const kw = ((data.keywords as string[]) || []).slice(0, 5);
        push({
          role: "assistant",
          kind: "text",
          text:
            `On it — looking for ${label || "matching companies"}.` +
            (kw.length ? ` Keying on: ${kw.join(", ")}.` : ""),
        });
        break;
      }
      case "candidates": {
        const items = ((data.prospects as Candidate[]) || []).map((p) => ({
          ...p,
          researching: true,
        }));
        candMsgRef.current = push({ role: "assistant", kind: "candidates", items });
        setStage("Researching and scoring each match");
        break;
      }
      case "scored":
        if (candMsgRef.current !== null) {
          patchCandidate(candMsgRef.current, data.index as number, {
            ...(data as Partial<Candidate>),
            researching: false,
          });
        }
        break;
      case "top":
        push({ role: "assistant", kind: "top", data: data as Candidate });
        setStage("Writing the opener around the evidence");
        break;
      case "draft":
        push({ role: "assistant", kind: "draft", data: data as Draft });
        break;
      case "draft_skip":
        push({ role: "assistant", kind: "text", text: (data.reason as string) || "" });
        break;
      case "no_pursue":
        push({
          role: "assistant",
          kind: "suggest",
          text: (data.reason as string) || "None of these cleared the pursue bar.",
          waitlist: true,
        });
        break;
      case "gmail_pending":
        push({ role: "assistant", kind: "gmail", text: (data.message as string) || "" });
        break;
      case "empty":
        push({
          role: "assistant",
          kind: "suggest",
          text: (data.reason as string) || "No matches found — try a different description.",
          waitlist: false,
        });
        break;
      case "error":
        push({
          role: "assistant",
          kind: "blocked",
          title: "Something went wrong",
          text: (data.reason as string) || "Please try again.",
          waitlist: false,
        });
        break;
      case "done":
        setStage("");
        break;
      default:
        break;
    }
  }

  async function run(ask: string, emailOverride?: string) {
    setRunning(true);
    setStage("Starting");
    const isSite = looksLikeWebsite(ask);

    let res: Response;
    try {
      res = await fetch("/api/demo/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          email: emailOverride ?? email,
          icp: isSite ? "" : ask,
          website: isSite ? ask : "",
          company,
        }),
      });
    } catch {
      push({
        role: "assistant",
        kind: "blocked",
        title: "Couldn't reach the demo",
        text: "Check your connection and try again.",
        waitlist: false,
      });
      setRunning(false);
      setStage("");
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
      const state = (payload.state as string) || "error";
      const message =
        (payload.message as string) ||
        (payload.error as string) ||
        "The demo is unavailable right now. Please try again shortly.";
      if (state === "need_email") {
        // The server rejected the address — re-open the gate with its reason.
        setPendingAsk(ask);
        setEmail("");
        push({ role: "assistant", kind: "email_gate", note: message });
      } else {
        const title =
          state === "capacity"
            ? "Today's demo capacity is full"
            : state === "rate_limited"
              ? "That's all for now"
              : state === "in_progress"
                ? "A run is already going"
                : "The demo is unavailable";
        push({
          role: "assistant",
          kind: "blocked",
          title,
          text: message,
          waitlist: ["capacity", "rate_limited", "in_progress"].includes(state),
        });
      }
      setRunning(false);
      setStage("");
      return;
    }

    if (!res.body) {
      push({
        role: "assistant",
        kind: "blocked",
        title: "The stream didn't open",
        text: "Please try again.",
        waitlist: false,
      });
      setRunning(false);
      setStage("");
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
      push({
        role: "assistant",
        kind: "blocked",
        title: "The stream was interrupted",
        text: "Please try again.",
        waitlist: false,
      });
    }
    setRunning(false);
    setStage("");
  }

  const gateSatisfied = emailish(email);

  return (
    <div className="mx-auto w-full max-w-3xl">
      <div className="glass flex h-[600px] flex-col overflow-hidden rounded-2xl border border-border shadow-card md:h-[660px]">
        {/* ── Window header, like the product's chat nav ─────────────── */}
        <div className="flex items-center gap-2.5 border-b border-border-faint px-4 py-3">
          <Logo className="w-5" />
          <div className="text-sm font-semibold text-text">
            Saqua <span className="font-normal text-muted">— AI sales assistant</span>
          </div>
          <span className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-border bg-white/60 px-2.5 py-1 text-[11px] text-muted">
            <span className="size-1.5 animate-pulse rounded-full bg-accent" /> Live · no account
          </span>
        </div>

        {/* ── Conversation ───────────────────────────────────────────── */}
        <div
          ref={scrollRef}
          className="flex-1 space-y-4 overflow-y-auto overscroll-contain px-4 py-5 md:px-6"
        >
          {messages.length === 0 && !running ? (
            <EmptyState onPick={send} />
          ) : (
            messages.map((m) => (
              <MessageView
                key={m.id}
                m={m}
                running={running}
                gateSatisfied={gateSatisfied}
                emailDraft={emailDraft}
                onEmailDraft={setEmailDraft}
                onEmailSubmit={submitEmail}
                honeypot={company}
                onHoneypot={setCompany}
                onPick={send}
              />
            ))
          )}
          {running && <StatusRow stage={stage} />}
        </div>

        {/* ── Composer, like the product's ───────────────────────────── */}
        <div className="border-t border-border-faint p-3">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
          >
            <Composer
              value={input}
              onChange={setInput}
              onSend={() => send(input)}
              disabled={running}
            />
          </form>
          <p className="mt-2 flex items-center justify-center gap-1.5 text-center text-[11px] text-muted">
            <Lock className="size-3" />
            {gateSatisfied
              ? `Runs the real research pipeline on live data — as ${email}.`
              : "Runs the real research pipeline on live data. No account — just a work email before the first run."}
          </p>
        </div>
      </div>
    </div>
  );
}

// ── Message rendering ────────────────────────────────────────────────
function MessageView({
  m,
  running,
  gateSatisfied,
  emailDraft,
  onEmailDraft,
  onEmailSubmit,
  honeypot,
  onHoneypot,
  onPick,
}: {
  m: Msg;
  running: boolean;
  gateSatisfied: boolean;
  emailDraft: string;
  onEmailDraft: (v: string) => void;
  onEmailSubmit: (e: React.FormEvent) => void;
  honeypot: string;
  onHoneypot: (v: string) => void;
  onPick: (text: string) => void;
}) {
  if (m.role === "user") {
    return (
      <Row user>
        <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-accent-soft px-4 py-2.5 text-sm leading-6 text-text">
          {m.text}
        </div>
      </Row>
    );
  }
  switch (m.kind) {
    case "text":
      return (
        <Row>
          <Bubble>{m.text}</Bubble>
        </Row>
      );
    case "email_gate":
      return (
        <Row>
          <div className="w-full max-w-md space-y-2.5">
            <Bubble>
              {m.note ||
                "Happy to — this runs Saqua's real research pipeline, so I just need a work email first. No account, no password."}
            </Bubble>
            <form onSubmit={onEmailSubmit} className="flex items-center gap-2">
              {/* Honeypot — hidden from humans; a bot that fills it gets a benign non-run. */}
              <input
                type="text"
                name="company"
                value={honeypot}
                onChange={(e) => onHoneypot(e.target.value)}
                tabIndex={-1}
                autoComplete="off"
                aria-hidden="true"
                className="pointer-events-none absolute left-[-9999px] size-0 opacity-0"
              />
              <input
                type="email"
                required
                value={emailDraft}
                onChange={(e) => onEmailDraft(e.target.value)}
                placeholder="Your work email"
                aria-label="Work email"
                disabled={gateSatisfied && !m.note}
                className="h-10 flex-1 rounded-lg border border-border-strong bg-white px-3 text-sm text-text outline-none transition-all placeholder:text-faint focus:border-accent-line focus:shadow-[0_0_0_4px_var(--accent-soft)] disabled:opacity-60"
              />
              <button
                type="submit"
                disabled={running || (gateSatisfied && !m.note)}
                className="inline-flex h-10 items-center gap-1.5 rounded-lg bg-accent px-4 text-sm font-semibold text-white transition-all hover:bg-accent-hi disabled:cursor-not-allowed disabled:opacity-60"
              >
                Start <ArrowUp className="size-3.5" />
              </button>
            </form>
          </div>
        </Row>
      );
    case "candidates":
      return (
        <Row wide>
          <Card className="w-full overflow-hidden !rounded-xl hover:!translate-y-0">
            <div className="flex items-center gap-2 border-b border-border-faint px-4 py-3 text-sm font-semibold text-text">
              <Search className="size-4 text-accent" /> Candidates, scored by fit
            </div>
            <div className="grid gap-3 p-4 sm:grid-cols-2">
              {m.items.map((c) => (
                <CandidateCard key={c.index} c={c} />
              ))}
            </div>
          </Card>
        </Row>
      );
    case "top":
      return (
        <Row wide>
          <Card className="w-full overflow-hidden !rounded-xl hover:!translate-y-0">
            <div className="border-b border-border-faint bg-accent-soft/40 px-4 py-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-accent">
                <Sparkles className="size-4" /> Best fit — {m.data.company}
              </div>
              {m.data.person?.name && (
                <div className="mt-1 flex items-center gap-1.5 text-xs text-text-2">
                  <UserRound className="size-3.5 text-accent" />
                  {m.data.person.name}
                  {m.data.person.role ? ` — ${m.data.person.role}` : ""}
                  <span className="text-muted">· found by research</span>
                </div>
              )}
            </div>
            {m.data.signal?.text && (
              <div className="space-y-2 px-4 py-4">
                <div className="text-xs font-semibold uppercase tracking-wide text-muted">
                  The signal it found
                </div>
                <p className="text-sm leading-6 text-text-2">{m.data.signal.text}</p>
                {m.data.signal.quote && (
                  <blockquote className="flex gap-2 border-l-2 border-accent-line pl-3 text-sm italic leading-6 text-muted">
                    <Quote className="mt-0.5 size-3.5 shrink-0 text-accent" />
                    <span>{m.data.signal.quote}</span>
                  </blockquote>
                )}
                {m.data.signal.source_url && (
                  <a
                    href={m.data.signal.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-block text-xs text-accent hover:underline"
                  >
                    {sourceLabel(m.data.signal.source_url)}
                  </a>
                )}
              </div>
            )}
          </Card>
        </Row>
      );
    case "draft":
      return (
        <Row wide>
          <Card className="w-full overflow-hidden !rounded-xl hover:!translate-y-0">
            <div className="flex flex-wrap items-center gap-2 border-b border-border-faint px-4 py-3">
              <span className="text-xs font-semibold uppercase tracking-wide text-muted">
                The opener it wrote
              </span>
              <GuardChip guard={m.data.guard} />
              {m.data.company && (
                <span className="ml-auto font-mono text-[11px] text-muted">{m.data.company}</span>
              )}
            </div>
            <div className="px-5 py-4">
              {m.data.subject && (
                <div className="border-b border-border-faint pb-2 font-[family-name:var(--font-serif)] text-base font-medium text-text">
                  {m.data.subject}
                </div>
              )}
              <p className="whitespace-pre-wrap pt-3 font-[family-name:var(--font-serif)] text-[15px] leading-7 text-text-2">
                {m.data.body}
              </p>
            </div>
          </Card>
        </Row>
      );
    case "suggest":
      return (
        <Row>
          <div className="w-full space-y-2.5">
            <Bubble>{m.text}</Bubble>
            <div className="flex flex-wrap items-center gap-1.5 pl-1">
              <span className="text-xs text-muted">Try one that matches well:</span>
              {EXAMPLE_PROMPTS.map((p) => (
                <button
                  key={p}
                  type="button"
                  disabled={running}
                  onClick={() => onPick(p)}
                  className="rounded-full border border-border bg-white/70 px-3 py-1 text-xs text-muted transition-colors hover:border-accent-line hover:text-accent disabled:opacity-50"
                >
                  {p}
                </button>
              ))}
            </div>
            {m.waitlist && (
              <div className="max-w-md pt-1">
                <HeroForm source="demo_no_pursue" label="Join the waitlist" />
              </div>
            )}
          </div>
        </Row>
      );
    case "gmail":
      return (
        <Row wide>
          <div className="w-full rounded-xl border border-accent-line bg-accent-soft/50 p-5">
            <div className="flex items-start gap-3">
              <div className="grid size-9 shrink-0 place-items-center rounded-full bg-white text-accent shadow-sm">
                <Lock className="size-4" />
              </div>
              <div className="min-w-0">
                <p className="text-sm leading-6 text-text-2">{m.text}</p>
                <div className="mt-3 max-w-md">
                  <HeroForm source="demo_done" label="Join the waitlist" />
                </div>
              </div>
            </div>
          </div>
        </Row>
      );
    case "blocked":
      return (
        <Row>
          <div className="w-full max-w-md space-y-2.5">
            <Bubble>
              <span className="font-medium text-text">{m.title}.</span> {m.text}
            </Bubble>
            {m.waitlist && <HeroForm source="demo_blocked" label="Join the waitlist" />}
          </div>
        </Row>
      );
    default:
      return null;
  }
}

/** One conversation row: assistant rows carry the product's logo avatar; `wide`
 * rows are inline result cards that span the column, like cards in the real chat. */
function Row({
  children,
  user = false,
  wide = false,
}: {
  children: React.ReactNode;
  user?: boolean;
  wide?: boolean;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 5 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22 }}
      className={cn("flex gap-3", user ? "justify-end" : "justify-start")}
    >
      {!user && (
        <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-white/70">
          <Logo className="w-4" />
        </div>
      )}
      <div className={cn("min-w-0", wide ? "w-full" : "flex")}>{children}</div>
      {user && (
        <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-white/70 text-muted">
          <UserRound className="size-3.5" />
        </div>
      )}
    </motion.div>
  );
}

function Bubble({ children }: { children: React.ReactNode }) {
  return (
    <div className="max-w-[85%] whitespace-pre-wrap rounded-lg border border-border-faint bg-white/60 px-4 py-2.5 text-sm leading-6 text-text-2">
      {children}
    </div>
  );
}

/** Live status row while the pipeline runs — the product's "Thinking" pattern. */
function StatusRow({ stage }: { stage: string }) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-white/70">
        <Logo className="w-4 animate-pulse" />
      </div>
      <div className="inline-flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded-lg border border-border-faint bg-white/60 px-4 py-2.5 text-sm text-text-2">
        <Search className="size-3.5 animate-pulse text-accent" />
        <span>{stage || "Working"}…</span>
        <span className="text-xs text-muted">{RUN_NOTE}</span>
      </div>
    </div>
  );
}

function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="flex h-full min-h-[320px] flex-col items-center justify-center text-center">
      <div className="grid size-14 place-items-center rounded-2xl border border-border bg-white/70 shadow-sm">
        <Logo className="w-8" />
      </div>
      <h2 className="mt-4 text-lg font-semibold text-text">Who should we go after?</h2>
      <p className="mx-auto mt-1.5 max-w-sm text-sm leading-6 text-muted">
        Tell me who you sell to — or paste your website. I&apos;ll find matching companies,
        research and score each one, and write the opener around real evidence.
      </p>
      <div className="mx-auto mt-4 flex max-w-lg flex-wrap items-center justify-center gap-1.5">
        {EXAMPLE_PROMPTS.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => onPick(p)}
            className="rounded-full border border-border bg-white/70 px-3 py-1.5 text-xs text-muted transition-colors hover:border-accent-line hover:bg-white hover:text-accent"
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}

function Composer({
  value,
  onChange,
  onSend,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  disabled: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  // Auto-grow: reset to measure, then clamp — same behavior as the product's.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [value]);

  return (
    <div className="flex items-end gap-2 rounded-lg border border-border bg-white/80 p-2 shadow-pop backdrop-blur">
      <textarea
        ref={ref}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            onSend();
          }
        }}
        rows={1}
        maxLength={300}
        placeholder="Find prospects for what you sell — or paste your website…"
        aria-label="Describe who you sell to"
        className="max-h-[160px] min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-sm text-text outline-none placeholder:text-muted"
      />
      <button
        type="submit"
        disabled={disabled || !value.trim()}
        aria-label="Send"
        className="flex size-9 shrink-0 items-center justify-center rounded-md bg-accent text-white transition-all hover:bg-accent-hi disabled:pointer-events-none disabled:opacity-40"
      >
        {disabled ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
      </button>
    </div>
  );
}

// ── Small pieces shared with the previous console ────────────────────
function sourceLabel(url: string): string {
  try {
    return "Source: " + new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "Source";
  }
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

/** Fit-level badge derived from the SAME score shown on the card, so the badge,
 * the number, and the Pursue/Skip recommendation never contradict each other.
 * The weak↔moderate cut sits at the Pursue threshold (45) on purpose. */
function scoreFitLevel(score: number): string {
  if (score >= 65) return "strong";
  if (score >= 45) return "moderate";
  return "weak";
}

function CandidateCard({ c }: { c: Candidate }) {
  const score = useCountUp(typeof c.fit_score === "number" ? c.fit_score : null);
  const level = typeof c.fit_score === "number" ? scoreFitLevel(c.fit_score) : "";
  return (
    <div className="flex flex-col rounded-lg border border-border-faint bg-white/70 p-4 motion-safe:animate-fade-up">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-display text-base font-semibold text-text">{c.company}</div>
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
              <div className="font-mono text-xl font-semibold leading-none text-accent">
                {score ?? "—"}
              </div>
              <div className="text-[10px] uppercase tracking-wide text-muted">/ 100</div>
            </div>
          )}
        </div>
      </div>

      {!c.researching && !c.timed_out && typeof c.fit_score === "number" && (
        <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-black/[0.06]">
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-700 ease-smooth"
            style={{ width: `${Math.max(4, Math.min(100, c.fit_score))}%` }}
          />
        </div>
      )}

      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        {level && <FitBadge level={level} />}
        {c.recommendation && <RecoBadge reco={c.recommendation} />}
      </div>

      <p className="mt-2 line-clamp-2 text-xs leading-5 text-text-2">
        {c.researching
          ? "Researching the site for who they are and any recent buying signal…"
          : c.timed_out
            ? "Ran out of time this run."
            : c.why || c.why_discovered}
      </p>
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
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[11px] font-medium capitalize",
        map[level] || map.unknown,
      )}
    >
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
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
        style[reco] || style.reject,
      )}
    >
      {reco === "reject" ? <X className="size-3" /> : <Check className="size-3" />}
      {label[reco] || reco}
    </span>
  );
}

/** Count a number up from 0 once, respecting prefers-reduced-motion. The final
 * value is guaranteed even if rAF never fires (throttled/paused in a background
 * tab) via a timeout fallback — the animation is only a flourish, never the
 * source of truth for the score. */
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
    const settle = window.setTimeout(() => setValue(target), dur + 400);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(settle);
    };
  }, [target]);
  return value;
}
