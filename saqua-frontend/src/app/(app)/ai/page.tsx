"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUp, Loader2, AlertTriangle, User, Search } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
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
  draft_email: (c) => `Draft a cold email for ${c}.`,
  draft_x_reply: (c) => `Draft an X (Twitter) reply for ${c} (I'll paste the post).`,
  draft_reddit_comment: (c) => `Draft a Reddit comment for ${c} (I'll paste the thread).`,
  draft_hn_reply: (c) => `Draft a Hacker News reply for ${c} (I'll paste the thread).`,
  draft_contact_form: (c) => `Draft a contact-form message for ${c}.`,
};

export default function AIChatPage() {
  const { activeId, setActive, refresh } = useChatNav();
  const [convId, setConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [streamAt, setStreamAt] = useState(-1); // index of the message to reveal progressively
  const [artifact, setArtifact] = useState<{ idx: number; data: EmailCardData } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Load whichever conversation the sidebar selected (or reset for a new chat).
  useEffect(() => {
    if (activeId === convId) return;
    if (!activeId) {
      setConvId(null);
      setMessages([]);
      setArtifact(null);
      setStreamAt(-1);
      setError("");
      return;
    }
    let cancelled = false;
    setArtifact(null);
    setStreamAt(-1);
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

      const res = await api.sendMessage(id, trimmed);
      setSending(false);
      setPending(null);
      if (!res.ok) {
        setError(reachError(res.error));
        return;
      }
      const next = res.data.messages || [];
      setMessages(next);
      // Reveal the last assistant message's narration progressively.
      setStreamAt(next.length && next[next.length - 1].role === "assistant" ? next.length - 1 : -1);
      refresh(); // sidebar Recents — the title may have been auto-generated this turn
    },
    [convId, sending, setActive, refresh],
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
                  streaming={i === streamAt}
                  onAction={onAction}
                  busy={sending}
                  activeDraftIdx={artifact?.idx ?? null}
                  index={i}
                  onOpenDraft={(data) => setArtifact({ idx: i, data })}
                />
              ))
            )}
            {pending && <Bubble role="user">{pending}</Bubble>}
            {sending && <Thinking />}
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
        <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-white/[0.03]">
          <Logo className="w-4" />
        </div>
      )}
      <div
        className={cn(
          "max-w-[76ch] whitespace-pre-wrap rounded-lg px-4 py-2.5 text-sm leading-6",
          isUser ? "bg-accent-soft text-text" : "border border-border-faint bg-white/[0.02] text-text-2",
        )}
      >
        {text ? shown : children}
      </div>
      {isUser && (
        <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-white/[0.03] text-muted">
          <User className="size-3.5" />
        </div>
      )}
    </motion.div>
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

const STAGES = ["Researching the web", "Scoring fit", "Writing the draft"];

function Thinking() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setI((v) => (v + 1) % STAGES.length), 1400);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="flex items-center gap-3">
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-white/[0.03]">
        <Logo className="w-4 animate-pulse-soft" />
      </div>
      <div className="inline-flex items-center gap-2.5 rounded-lg border border-border-faint bg-white/[0.02] px-4 py-2.5 text-sm text-text-2">
        <Search className="size-3.5 animate-pulse-soft text-accent-hi" />
        <span>{STAGES[i]}…</span>
        <span className="text-xs text-muted">research runs live, so this can take a minute</span>
      </div>
    </div>
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
        <div className="relative grid size-14 place-items-center rounded-2xl border border-border bg-white/[0.03]">
          <Logo className="w-8" />
        </div>
      </div>
      <h1 className="text-xl font-semibold text-text">Who should we go after?</h1>
      <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-text-2">
        Describe who to find, or paste a list of companies. Saqua researches each one, scores fit,
        and drafts the opener — right here. Nothing sends without you.
      </p>
      {/* Starter prompts: quiet, secondary chips — not the visual focus. */}
      <div className="mx-auto mt-5 flex max-w-xl flex-wrap items-center justify-center gap-1.5">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            onClick={() => onPick(ex)}
            className="rounded-full border border-border-faint bg-white/[0.02] px-3 py-1.5 text-xs text-muted transition-colors hover:border-border hover:bg-white/[0.05] hover:text-text-2"
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
