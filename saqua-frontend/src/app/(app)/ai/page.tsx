"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, Loader2, AlertTriangle, User, Check, Search } from "lucide-react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Logo } from "@/components/ui/logo";
import { cn } from "@/lib/utils";
import { ProspectsCard } from "@/components/chat/prospects-card";
import { ChannelCard } from "@/components/chat/channel-card";
import { DraftCard } from "@/components/chat/draft-card";
import { StatsCard } from "@/components/chat/stats-card";
import { RepliesCard } from "@/components/chat/replies-card";
import { CampaignsCard } from "@/components/chat/campaigns-card";
import { ResearchTrail } from "@/components/chat/research-trail";
import { ArtifactPanel } from "@/components/chat/artifact-panel";
import { useStreamedText } from "@/components/chat/use-streamed-text";
import { useChatNav } from "@/components/chat/chat-nav";
import { useDemo } from "@/components/demo/demo-provider";
import { OnboardingTour } from "@/components/onboarding/tour";
import {
  api,
  type ChatMessage,
  type ProspectEntry,
  type ProspectsCardData,
  type ChannelCardData,
  type EmailCardData,
  type ResearchCardData,
  type StatsCardData,
  type RepliesCardData,
  type CampaignsCardData,
  type ResearchTrailEvent,
} from "@/lib/api";

const EXAMPLES = [
  "Find SaaS founders hiring an SDR",
  "Score my list: Stripe, Ramp, Linear",
  "Research linear.app as a prospect",
];

const ACTION_PROMPT: Record<string, (company: string) => string> = {
  // Discovered leads are not researched yet, so their one action is to research.
  research_prospect: (c) => `Research ${c} properly and score it as a prospect.`,
  draft_email: (c) => `Draft a cold email for ${c}.`,
  draft_x_reply: (c) => `Draft an X (Twitter) reply for ${c} (I'll paste the post).`,
  draft_reddit_comment: (c) => `Draft a Reddit comment for ${c} (I'll paste the thread).`,
  draft_hn_reply: (c) => `Draft a Hacker News reply for ${c} (I'll paste the thread).`,
  draft_contact_form: (c) => `Draft a contact-form message for ${c}.`,
};

export default function AIChatPage() {
  const { activeId, openNonce, setActive, refresh } = useChatNav();
  // Demo sessions meter turns server-side; refreshing right after a send keeps
  // the banner's remaining-messages count and the engagement prompt honest
  // instead of waiting up to a minute for the background poll.
  const { isDemo, turnsLeft, refresh: refreshDemo, noteTurnUsed } = useDemo();
  const [convId, setConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  // The in-flight turn's live trace, streamed from the backend: `step` is WHAT it
  // is doing, `thought` is WHY (grounded in the run's real state, never canned).
  const [steps, setSteps] = useState<TraceLine[]>([]);
  // The canonical, evidence-bearing research trail: the persisted trail on load,
  // plus live events (deduped by event_id) as a turn streams. One view serves both
  // so nothing is ever replayed as if it were happening again.
  const [trail, setTrail] = useState<ResearchTrailEvent[]>([]);
  const [artifact, setArtifact] = useState<{ idx: number; data: EmailCardData } | null>(null);
  // The TRUE active research target, from the server (workspace.company). Shown as a
  // persistent "Researching: X" chip and updated the moment a turn changes it.
  const [activeCompany, setActiveCompany] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Index boundary marking where the CURRENT turn's assistant output begins. Any
  // message before it belongs to a prior turn and must never be re-animated — that
  // was the "old HackerRank paragraph types itself out again while the new request
  // is still processing" bug: before the first new token arrived, the newest
  // assistant message WAS the previous turn's, so the typewriter replayed it.
  const turnStartRef = useRef(0);

  // The assistant text that should type itself out: the newest assistant prose in
  // the transcript, but only within the current turn. History renders in full, and
  // the gap between "send" and the first new token animates nothing.
  const typingIdx = useMemo(() => {
    if (!sending) return -1;
    for (let i = messages.length - 1; i >= turnStartRef.current; i--) {
      const m = messages[i];
      if (m.role === "assistant" && (m.kind === "text" || m.kind === "notice")) return i;
    }
    return -1;
  }, [messages, sending]);

  // Load whichever conversation the sidebar selected (or reset for a new chat).
  // Re-runs on openNonce too, so clicking a Recents item whose load just failed
  // retries the fetch even though activeId is unchanged. The activeId===convId
  // guard still skips a needless refetch of an already-loaded conversation
  // (after an error convId is null, so a retry falls through to the fetch).
  useEffect(() => {
    if (activeId === convId) return;
    if (!activeId) {
      setConvId(null);
      setMessages([]);
      setArtifact(null);
      setSteps([]);
      setTrail([]);
      setError("");
      setActiveCompany(null);
      return;
    }
    let cancelled = false;
    setArtifact(null);
    setSteps([]);
    setTrail([]);
    setError("");
    void api.conversation(activeId).then((res) => {
      if (cancelled) return;
      if (!res.ok) {
        setError(reachError(res.error));
        return;
      }
      setConvId(activeId);
      const loaded = res.data.messages || [];
      // Loaded history is complete: nothing in it should type itself out.
      turnStartRef.current = loaded.length;
      setMessages(loaded);
      setActiveCompany(res.data.active_company ?? null);
      setTrail(res.data.research_trail ?? []);   // restore the persisted trail
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, openNonce]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, pending, sending]);

  // Broadcast "an agent turn is running" so app-wide surfaces (the upgrade prompt)
  // never interrupt an active stream. Cleared when the turn settles.
  useEffect(() => {
    window.dispatchEvent(new CustomEvent("saqua:busy", { detail: sending }));
    return () => {
      window.dispatchEvent(new CustomEvent("saqua:busy", { detail: false }));
    };
  }, [sending]);

  // A demo visitor who has spent every message can't send another — the server
  // would 429 anyway, so stop it here and point at the waitlist instead.
  const demoExhausted = isDemo && turnsLeft === 0;

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;
      if (isDemo && turnsLeft === 0) {
        setError("You've used every message in this demo. Join the waitlist to keep going.");
        return;
      }
      setError("");
      setInput("");
      setPending(trimmed);
      setSteps([]);
      setSending(true);

      let id = convId;
      if (!id) {
        const created = await api.createConversation();
        if (!created.ok) {
          setError(reachError(created.error));
          setSending(false);
          setPending(null);
          return;
        }
        id = created.data.id;
        setConvId(id);
        setActive(id); // highlight in the sidebar without triggering a reload
      }

      // Commit the user's message into the transcript, then stream the reply. The
      // pending bubble hands off to this so there is no flicker between the two.
      // Mark the turn boundary at the first slot AFTER this user message: only
      // assistant messages from here on are "live" and may animate.
      setMessages((prev) => {
        turnStartRef.current = prev.length + 1;
        return [...prev, { role: "user", kind: "text", content: trimmed, data: null }];
      });
      setPending(null);
      // Move the banner the instant we send; refreshDemo() below reconciles to the
      // server's authoritative count once the turn is reserved.
      if (isDemo) noteTurnUsed();

      const append = (kind: TraceLine["kind"]) => (text: string) =>
        setSteps((prev) =>
          prev[prev.length - 1]?.text === text ? prev : [...prev, { kind, text }],
        );

      const res = await api.sendMessageStream(id, trimmed, {
        // Real pipeline stages, in order — the "AI doing work" checklist.
        onStep: append("step"),
        // The reasoning between stages ("Most of these are recruiters, dropping them").
        onThought: append("thought"),
        // Canonical trail events (real tool start/finish/failure + evidence).
        // Dedupe by event_id so an SSE reconnect never double-appends.
        onTrail: (ev) =>
          setTrail((prev) =>
            prev.some((e) => e.event_id === ev.event_id)
              ? prev.map((e) => (e.event_id === ev.event_id ? ev : e))
              : [...prev, ev],
          ),
        // Each assistant message (narration, a card, the reply) as it is produced.
        onMessage: (m) => setMessages((prev) => [...prev, m]),
        onError: (msg) => setError(reachError(msg)),
        onDone: () => setSteps([]),
        // The final canonical state carries the true active target AND the
        // authoritative persisted trail, so we reconcile any event a reconnect
        // might have missed.
        onFinal: (conv) => {
          setActiveCompany(conv.active_company ?? null);
          if (conv.research_trail) setTrail(conv.research_trail);
        },
      });

      setSending(false);
      setSteps([]);
      if (!res.ok && res.error) setError(reachError(res.error));
      refresh(); // sidebar Recents — the title may have been auto-generated this turn
      refreshDemo(); // demo only: turns used just changed
    },
    [convId, sending, setActive, refresh, refreshDemo, isDemo, turnsLeft, noteTurnUsed],
  );

  const onAction = useCallback(
    (action: string, prospect: ProspectEntry) => {
      const build = ACTION_PROMPT[action];
      if (build) send(build(prospect.company));
    },
    [send],
  );

  const empty = messages.length === 0 && !pending && !sending;

  return (
    <div className="-mx-5 -mb-28 -mt-6 flex h-full overflow-hidden md:-mx-8 md:-my-8">
      <OnboardingTour />
      {/* Conversation (chat history now lives in the single app sidebar) */}
      <section data-tour="artifact-hint" className="flex min-w-0 flex-1 flex-col">
        {activeCompany && (
          <div className="flex items-center gap-2 border-b border-border-faint bg-black/[0.015] px-4 py-2 text-xs">
            <Search className="size-3.5 shrink-0 text-accent" />
            <span className="text-muted">Researching</span>
            <span className="truncate font-medium text-text">{activeCompany}</span>
          </div>
        )}
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-4 px-4 py-6">
            {empty ? (
              <EmptyState onPick={send} />
            ) : (
              messages.map((m, i) => (
                <MessageView
                  key={i}
                  message={m}
                  streaming={i === typingIdx}
                  onAction={onAction}
                  busy={sending}
                  activeDraftIdx={artifact?.idx ?? null}
                  index={i}
                  onOpenDraft={(data) => setArtifact({ idx: i, data })}
                />
              ))
            )}
            {pending && <Bubble role="user">{pending}</Bubble>}
            {sending && <LiveSteps steps={steps} />}
            {/* The canonical research trail — live during a turn, and restored on a
                reloaded thread — with clickable evidence. Inline (never a modal), so
                the composer stays visible on every screen size. */}
            {trail.length > 0 && <ResearchTrail events={trail} live={sending} defaultOpen={sending} />}
            {error && (
              <Card className="border-[color:var(--danger-soft)]">
                <div className="flex items-center gap-2.5 px-5 py-3 text-sm text-danger">
                  <AlertTriangle className="size-4 shrink-0" />
                  {error}
                </div>
              </Card>
            )}
          </div>
        </div>

        <div data-tour="composer" className="glass border-t border-border-faint p-3">
          <div className="mx-auto max-w-3xl">
            {demoExhausted ? (
              <div className="flex flex-col items-center gap-2 rounded-lg border border-accent-line bg-accent-soft/50 px-5 py-4 text-center">
                <p className="text-sm text-text-2">
                  That&apos;s every message in this demo. Join the waitlist to keep going with your own account.
                </p>
                <a
                  href="/#waitlist"
                  className="inline-flex h-9 items-center gap-1.5 rounded-full bg-accent px-4 text-xs font-semibold text-[color:var(--accent-ink)] transition-colors hover:bg-accent-hi"
                >
                  Join the waitlist →
                </a>
              </div>
            ) : (
              <Composer
                value={input}
                onChange={setInput}
                onSend={() => send(input)}
                disabled={sending}
              />
            )}
          </div>
        </div>
      </section>

      {/* Artifact panel — the draft canvas (serif copy lives here). Slides in/out. */}
      <AnimatePresence>
        {artifact && (
          <motion.aside
            key="artifact"
            initial={{ x: 40, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: 40, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.22, 0.61, 0.36, 1] }}
            className="fixed inset-y-0 right-0 z-40 w-full max-w-md lg:static lg:z-auto lg:w-[380px] lg:max-w-none xl:w-[420px]"
          >
            <ArtifactPanel draft={artifact.data} onClose={() => setArtifact(null)} />
          </motion.aside>
        )}
      </AnimatePresence>
    </div>
  );
}

// ── One message, rendered by kind ──────────────────────────────────────
function MessageView({
  message: m,
  streaming,
  onAction,
  busy,
  activeDraftIdx,
  index,
  onOpenDraft,
}: {
  message: ChatMessage;
  streaming: boolean;
  onAction: (action: string, p: ProspectEntry) => void;
  busy: boolean;
  activeDraftIdx: number | null;
  index: number;
  onOpenDraft: (data: EmailCardData) => void;
}) {
  if (m.role === "user") return <Bubble role="user">{m.content}</Bubble>;

  const narration = m.content ? (
    <Bubble role="assistant" streaming={streaming}>
      {m.content}
    </Bubble>
  ) : null;

  if (m.kind === "prospects") {
    return (
      <div className="space-y-2">
        {narration}
        <ProspectsCard data={(m.data as ProspectsCardData) || { prospects: [] }} onAction={onAction} busy={busy} />
      </div>
    );
  }
  if (m.kind === "channel") {
    return (
      <div className="space-y-2">
        {narration}
        <ChannelCard data={m.data as ChannelCardData} />
      </div>
    );
  }
  if (m.kind === "email") {
    return (
      <div className="space-y-2">
        {narration}
        <DraftCard
          data={m.data as EmailCardData}
          active={activeDraftIdx === index}
          onOpen={() => onOpenDraft(m.data as EmailCardData)}
        />
      </div>
    );
  }
  if (m.kind === "research") {
    return (
      <div className="space-y-2">
        {narration}
        <ResearchCard data={m.data as ResearchCardData} />
      </div>
    );
  }
  if (m.kind === "stats") {
    return (
      <div className="space-y-2">
        {narration}
        <StatsCard data={m.data as StatsCardData} />
      </div>
    );
  }
  if (m.kind === "replies") {
    return (
      <div className="space-y-2">
        {narration}
        <RepliesCard data={m.data as RepliesCardData} />
      </div>
    );
  }
  if (m.kind === "campaigns") {
    return (
      <div className="space-y-2">
        {narration}
        <CampaignsCard data={m.data as CampaignsCardData} />
      </div>
    );
  }
  return <Bubble role="assistant" streaming={streaming}>{m.content}</Bubble>;
}

function Bubble({
  role,
  streaming = false,
  children,
}: {
  role: "user" | "assistant";
  streaming?: boolean;
  children: React.ReactNode;
}) {
  const isUser = role === "user";
  const text = typeof children === "string" ? children : "";
  const { shown } = useStreamedText(text, streaming && !isUser && text.length > 0);
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className={cn("flex gap-3", isUser ? "justify-end" : "justify-start")}
    >
      {!isUser && (
        <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-black/[0.03]">
          <Logo className="w-4" />
        </div>
      )}
      <div
        className={cn(
          "max-w-[76ch] whitespace-pre-wrap rounded-lg px-4 py-2.5 text-sm leading-6",
          isUser ? "bg-accent-soft text-text" : "border border-border-faint bg-black/[0.02] text-text-2",
        )}
      >
        {text ? <Emphasized text={shown} /> : children}
      </div>
      {isUser && (
        <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-black/[0.03] text-muted">
          <User className="size-3.5" />
        </div>
      )}
    </motion.div>
  );
}

/** Renders the one piece of markdown the assistant actually uses in prose: **bold**
 *  for a company name it is calling out. The bubble is plain text otherwise, so
 *  without this the user reads literal asterisks around every recommendation.
 *  Deliberately NOT a markdown renderer: this builds React nodes by splitting the
 *  string, so no HTML is ever interpreted and there is nothing to inject. */
function Emphasized({ text }: { text: string }) {
  if (!text.includes("**")) return <>{text}</>;
  const parts = text.split(/\*\*(.+?)\*\*/gs); // odd indexes are the bolded runs
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <strong key={i} className="font-medium text-text">
            {part}
          </strong>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function ResearchCard({ data }: { data: ResearchCardData }) {
  const pages = data.pages_crawled?.length ?? 0;
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-faint px-5 py-3">
        <span className="text-sm font-medium text-text">{data.company || "Research"}</span>
        {typeof data.research_score === "number" && (
          <Badge tone="neutral">
            <span className="font-mono">{data.research_score}</span>
          </Badge>
        )}
        {pages > 0 && <span className="font-mono text-xs text-muted">{pages} pages</span>}
      </div>
      {data.what_they_do && <div className="px-5 py-4 text-sm leading-6 text-text-2">{data.what_they_do}</div>}
    </Card>
  );
}

/** One line of the live trace: a `step` (what the agent is doing) or a `thought`
 *  (why, derived from the run's real state). */
type TraceLine = { kind: "step" | "thought"; text: string };

/** The agent working, streamed from the backend. Steps are the spine, each with a
 *  spinner while live and a check once passed. Thoughts sit under them as the
 *  reasoning that produced the next decision ("Most of these are recruiters, they
 *  post other companies' roles"), which is what makes this read as a colleague
 *  thinking rather than a pipeline executing. Reduced-motion gets a static list. */
function LiveSteps({ steps }: { steps: TraceLine[] }) {
  const reduced = useReducedMotion();
  // Reasoning lines are longer than stage labels, so keep a slightly deeper tail.
  const visible = steps.slice(-7);
  const lastIdx = visible.length - 1;
  // Keys must be stable as the window scrolls. Keying on the position WITHIN the
  // slice renumbers every row once the tail starts moving, so AnimatePresence sees
  // the whole block exit and re-enter and the trace visibly duplicates mid-run.
  const firstAbs = steps.length - visible.length;
  return (
    <motion.div
      initial={reduced ? false : { opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="flex gap-3"
    >
      <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-black/[0.03]">
        <Logo className="w-4 animate-pulse-soft" />
      </div>
      <div className="min-w-0 max-w-[68ch] space-y-1 rounded-lg border border-border-faint bg-black/[0.02] px-4 py-3">
        {visible.length === 0 ? (
          <TraceRow line={{ kind: "step", text: "Thinking" }} active />
        ) : (
          <AnimatePresence initial={false} mode="popLayout">
            {visible.map((line, i) => (
              <TraceRow
                key={firstAbs + i}
                line={line}
                active={i === lastIdx}
                reduced={!!reduced}
              />
            ))}
          </AnimatePresence>
        )}
      </div>
    </motion.div>
  );
}

function TraceRow({
  line,
  active,
  reduced,
}: {
  line: TraceLine;
  active?: boolean;
  reduced?: boolean;
}) {
  const isThought = line.kind === "thought";
  return (
    <motion.div
      layout={!reduced}
      initial={reduced ? false : { opacity: 0, y: 3 }}
      // Reasoning stays legible after it scrolls past; stage labels recede once done.
      animate={{ opacity: active || isThought ? 1 : 0.5, y: 0 }}
      exit={reduced ? undefined : { opacity: 0, height: 0 }}
      transition={{ duration: 0.2 }}
      className={cn(
        "flex gap-2 text-sm leading-5",
        isThought ? "items-start pl-[1.375rem]" : "items-center",
      )}
    >
      {!isThought &&
        (active ? (
          <Loader2 className="size-3.5 shrink-0 animate-spin text-accent" />
        ) : (
          <Check className="size-3.5 shrink-0 text-muted" />
        ))}
      <span className={isThought ? "text-text-2" : active ? "text-text" : "text-muted"}>
        {line.text}
      </span>
    </motion.div>
  );
}

function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="flex min-h-[52vh] flex-col items-center justify-center text-center"
    >
      <div className="relative mb-5">
        <div className="accent-glow absolute inset-0 scale-[2.4] opacity-50" />
        <div className="relative grid size-14 place-items-center rounded-2xl border border-border bg-black/[0.03]">
          <Logo className="w-8" />
        </div>
      </div>
      <h1 className="text-xl font-semibold text-text">Who should we go after?</h1>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-text-2">
        Describe who to find, or paste a list of companies. Saqua researches each one, scores fit,
        and drafts the opener, right here. Nothing sends without you.
      </p>
      {/* Starter prompts: quiet, secondary chips — not the visual focus. */}
      <div className="mx-auto mt-5 flex max-w-xl flex-wrap items-center justify-center gap-1.5">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            onClick={() => onPick(ex)}
            className="rounded-full border border-border-faint bg-black/[0.02] px-3 py-1.5 text-xs text-muted transition-colors hover:border-border hover:bg-black/[0.05] hover:text-text-2"
          >
            {ex}
          </button>
        ))}
      </div>
    </motion.div>
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
  // Auto-grow: reset to measure, then clamp to a max height.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  return (
    <div className="rounded-lg border border-border bg-panel/90 p-2 shadow-pop backdrop-blur">
      <div className="flex items-end gap-2">
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
          placeholder="Find SaaS founders hiring an SDR…  (Enter to send, Shift+Enter for a new line)"
          className="max-h-[200px] min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-sm text-text outline-none placeholder:text-muted"
        />
        <button
          onClick={onSend}
          disabled={disabled || !value.trim()}
          aria-label="Send"
          className="flex size-9 shrink-0 items-center justify-center rounded-md bg-accent text-[color:var(--accent-ink)] transition-all hover:bg-accent-hi disabled:pointer-events-none disabled:opacity-40"
        >
          {disabled ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
        </button>
      </div>
    </div>
  );
}

/**
 * A reachability failure said the way the person reading it can act on.
 *
 * "Make sure the backend is running" was written for whoever was developing this
 * and is meaningless to the founder using it, who has no backend to start. What
 * they need is what happened, that their work survived, and what to do next.
 */
function reachError(err: string): string {
  const unreachable = /^HTTP 5\d\d$/.test(err) || err === "network error" || /proxy failed/i.test(err);
  return unreachable
    ? "Saqua couldn't reach the assistant, so that message wasn't answered. Your conversation is saved. " +
      "Try sending it again in a moment."
    : err || "That didn't go through. Your conversation is saved, so try sending it again.";
}
