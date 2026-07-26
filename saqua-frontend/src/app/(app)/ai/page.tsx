"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, Loader2, AlertTriangle, User, Check } from "lucide-react";
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
  const { activeId, setActive, refresh } = useChatNav();
  // Demo sessions meter turns server-side; refreshing right after a send keeps
  // the banner's remaining-messages count and the engagement prompt honest
  // instead of waiting up to a minute for the background poll.
  const { refresh: refreshDemo } = useDemo();
  const [convId, setConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  // The in-flight turn's live trace, streamed from the backend: `step` is WHAT it
  // is doing, `thought` is WHY (grounded in the run's real state, never canned).
  const [steps, setSteps] = useState<TraceLine[]>([]);
  const [artifact, setArtifact] = useState<{ idx: number; data: EmailCardData } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // The assistant text that should type itself out: the newest assistant prose in
  // the transcript, but only while a turn is streaming. History renders in full.
  const typingIdx = useMemo(() => {
    if (!sending) return -1;
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === "assistant" && (m.kind === "text" || m.kind === "notice")) return i;
    }
    return -1;
  }, [messages, sending]);

  // Load whichever conversation the sidebar selected (or reset for a new chat).
  useEffect(() => {
    if (activeId === convId) return;
    if (!activeId) {
      setConvId(null);
      setMessages([]);
      setArtifact(null);
      setSteps([]);
      setError("");
      return;
    }
    let cancelled = false;
    setArtifact(null);
    setSteps([]);
    setError("");
    void api.conversation(activeId).then((res) => {
      if (cancelled) return;
      if (!res.ok) {
        setError(reachError(res.error));
        return;
      }
      setConvId(activeId);
      setMessages(res.data.messages || []);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, pending, sending]);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;
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
      setMessages((prev) => [...prev, { role: "user", kind: "text", content: trimmed, data: null }]);
      setPending(null);

      const append = (kind: TraceLine["kind"]) => (text: string) =>
        setSteps((prev) =>
          prev[prev.length - 1]?.text === text ? prev : [...prev, { kind, text }],
        );

      const res = await api.sendMessageStream(id, trimmed, {
        // Real pipeline stages, in order — the "AI doing work" checklist.
        onStep: append("step"),
        // The reasoning between stages ("Most of these are recruiters, dropping them").
        onThought: append("thought"),
        // Each assistant message (narration, a card, the reply) as it is produced.
        onMessage: (m) => setMessages((prev) => [...prev, m]),
        onError: (msg) => setError(reachError(msg)),
        onDone: () => setSteps([]),
      });

      setSending(false);
      setSteps([]);
      if (!res.ok && res.error) setError(reachError(res.error));
      refresh(); // sidebar Recents — the title may have been auto-generated this turn
      refreshDemo(); // demo only: turns used just changed
    },
    [convId, sending, setActive, refresh, refreshDemo],
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
    <div className="-mx-5 -mb-28 -mt-6 flex h-[calc(100vh-var(--nav-h))] md:-mx-8 md:-my-8">
      <OnboardingTour />
      {/* Conversation (chat history now lives in the single app sidebar) */}
      <section data-tour="artifact-hint" className="flex min-w-0 flex-1 flex-col">
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
            <Composer value={input} onChange={setInput} onSend={() => send(input)} disabled={sending} />
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

function reachError(err: string): string {
  const unreachable = /^HTTP 5\d\d$/.test(err) || err === "network error" || /proxy failed/i.test(err);
  return unreachable
    ? `Couldn't reach the assistant (${err}). Make sure the backend is running, then try again.`
    : err || "Something went wrong. Please try again.";
}
