"use client";

import { useEffect, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Search,
  Radar,
  Sparkles,
  KanbanSquare,
  Inbox,
  Repeat,
  Check,
  Mail,
} from "lucide-react";
import { cn } from "@/lib/utils";

/* ── Mock chrome ───────────────────────────────────────────────────── */
function Mock({ title, icon: Icon, children }: { title: string; icon: LucideIcon; children: React.ReactNode }) {
  return (
    <div className="w-full overflow-hidden rounded-xl border border-border bg-card shadow-pop">
      <div className="flex items-center gap-2 border-b border-border-faint px-4 py-3 text-sm font-semibold text-text">
        <span className="grid size-6 place-items-center rounded-md bg-accent text-white">
          <Icon className="size-3.5" />
        </span>
        {title}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function FitBadge({ n = 92 }: { n?: number }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-accent-line bg-accent-soft px-2 py-0.5 text-accent">
      <span className="font-mono text-sm font-semibold leading-none">{n}</span>
      <span className="text-[10px] uppercase tracking-wide">fit</span>
    </span>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return <div className="flex items-center gap-2 rounded-md border border-border-faint bg-black/[0.02] px-3 py-2 text-xs text-text-2">{children}</div>;
}

/* ── Mocks per surface ─────────────────────────────────────────────── */
const ResearchMock = () => (
  <Mock title="Verified facts" icon={Search}>
    <div className="mb-3 flex items-start justify-between gap-3">
      <div>
        <div className="text-sm font-medium text-text">Linear</div>
        <div className="text-xs text-muted">linear.app · issue tracking for software teams</div>
      </div>
      <FitBadge n={92} />
    </div>
    <div className="space-y-2">
      <Row><span className="text-accent">“</span> Posted 2 GTM roles in the last 30 days</Row>
      <Row><span className="text-accent">“</span> Series B, 8 months ago · expanding motion</Row>
      <Row><span className="text-accent">“</span> Founder ships weekly; active on launches</Row>
    </div>
    <div className="mt-3 flex items-center justify-between border-t border-border-faint pt-3">
      <span className="inline-flex items-center gap-1.5 text-xs text-accent"><span className="size-1.5 rounded-full bg-accent" /> Qualified, worth pursuing</span>
      <span className="font-mono text-[10px] text-muted">3 sources</span>
    </div>
  </Mock>
);

const DiscoveryMock = () => (
  <Mock title="Prospects matched" icon={Radar}>
    <div className="space-y-2">
      {[
        ["Linear", "linear.app", 92],
        ["Vercel", "vercel.com", 88],
        ["Resend", "resend.com", 85],
      ].map(([n, d, f]) => (
        <div key={n as string} className="flex items-center justify-between rounded-md border border-border-faint bg-black/[0.02] px-3 py-2">
          <div>
            <div className="text-xs font-medium text-text">{n}</div>
            <div className="font-mono text-[10px] text-muted">{d}</div>
          </div>
          <FitBadge n={f as number} />
        </div>
      ))}
    </div>
    <div className="mt-3 flex items-center gap-2 rounded-md bg-accent-soft px-3 py-2 text-xs text-accent">
      <Radar className="size-3.5" /> <span className="font-mono font-semibold">312</span> companies matched your ICP
    </div>
  </Mock>
);

const CoachMock = () => (
  <Mock title="Sales assistant" icon={Sparkles}>
    <div className="mb-3 inline-flex items-center gap-2 rounded-md border border-border-faint bg-black/[0.02] px-2.5 py-1 font-mono text-[10px] text-muted">
      Deal: Linear · $15,000 · stage Contacted
    </div>
    <div className="space-y-2">
      <div className="ml-auto w-fit max-w-[80%] rounded-lg rounded-br-sm bg-accent-soft px-3 py-2 text-xs text-accent">How do I open the CTO without repeating myself?</div>
      <div className="w-fit max-w-[88%] rounded-lg rounded-bl-sm border border-border-faint bg-black/[0.02] px-3 py-2 text-xs leading-5 text-text-2">
        Lead with the Series B hiring signal, not the product. One line on why now, then ask for fifteen minutes.
      </div>
    </div>
  </Mock>
);

const PipelineMock = () => (
  <Mock title="Pipeline" icon={KanbanSquare}>
    <div className="grid grid-cols-3 gap-2">
      {[
        ["New", 2, "text-muted"],
        ["Contacted", 3, "text-accent"],
        ["Replied", 1, "text-[color:var(--success)]"],
      ].map(([label, count]) => (
        <div key={label as string} className="rounded-md border border-border-faint bg-black/[0.02] p-2">
          <div className="mb-2 flex items-center justify-between text-[10px] font-semibold uppercase tracking-wide text-muted">
            {label} <span className="font-mono">{count as number}</span>
          </div>
          <div className="space-y-1.5">
            {Array.from({ length: count as number }).map((_, i) => (
              <div key={i} className="h-6 rounded border border-border-faint bg-card" />
            ))}
          </div>
        </div>
      ))}
    </div>
  </Mock>
);

const InboxMock = () => (
  <Mock title="Inbox" icon={Inbox}>
    <div className="space-y-2">
      {[
        ["Karri @ Linear", "Re: your GTM hiring", "Replied", "text-[color:var(--success)] border-[color:var(--success-soft)] bg-[color:var(--success-soft)]"],
        ["Guillermo @ Vercel", "Quick one on scaling outbound", "Sent", "text-muted border-border-faint bg-black/[0.02]"],
        ["Zeno @ Resend", "Worth fifteen minutes?", "Follow-up 2", "text-[color:var(--warn)] border-[color:var(--warn-soft)] bg-[color:var(--warn-soft)]"],
      ].map(([who, subj, tag, cls]) => (
        <div key={who as string} className="flex items-center justify-between gap-2 rounded-md border border-border-faint bg-black/[0.02] px-3 py-2">
          <div className="min-w-0">
            <div className="truncate text-xs font-medium text-text">{who}</div>
            <div className="truncate text-[11px] text-muted">{subj}</div>
          </div>
          <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium", cls as string)}>{tag}</span>
        </div>
      ))}
    </div>
  </Mock>
);

const AUTOMATION_STEPS: [string, string, LucideIcon, string][] = [
  ["Day 0", "Opening email sent", Mail, "text-accent"],
  ["Day 3", "Follow-up · new angle", Repeat, "text-accent"],
  ["Day 7", "Follow-up · social proof", Repeat, "text-muted"],
  ["Reply", "Sequence stopped automatically", Check, "text-[color:var(--success)]"],
];

const AutomationMock = () => (
  <Mock title="Cadence" icon={Repeat}>
    <div className="space-y-2.5">
      {AUTOMATION_STEPS.map(([day, label, Icon, color], i) => (
        <div key={label} className="flex items-center gap-3">
          <div className="flex flex-col items-center">
            <span className={cn("grid size-6 place-items-center rounded-full border border-border-faint bg-card", color)}>
              <Icon className="size-3" />
            </span>
            {i < AUTOMATION_STEPS.length - 1 && <span className="mt-0.5 h-4 w-px bg-border" />}
          </div>
          <div className="flex flex-1 items-center justify-between">
            <span className="text-xs text-text-2">{label}</span>
            <span className="font-mono text-[10px] text-muted">{day}</span>
          </div>
        </div>
      ))}
    </div>
  </Mock>
);

/* ── Tab definitions ───────────────────────────────────────────────── */
const TABS: {
  label: string;
  eyebrow: string;
  title: string;
  body: string;
  mock: React.ReactNode;
}[] = [
  {
    label: "Research",
    eyebrow: "Adaptive research",
    title: "It reads the company, not a database row.",
    body: "Saqua reads the actual site, funding news, and hiring signals, then extracts verified facts with the quote that backs each one. If it isn't on the page, it never reaches the email.",
    mock: <ResearchMock />,
  },
  {
    label: "Discovery",
    eyebrow: "Prospect discovery",
    title: "Find the companies actually worth reaching out to.",
    body: "Describe your ICP once. Saqua surfaces matching companies, scores each for fit, and filters out the low-fit noise before you spend a single send on it.",
    mock: <DiscoveryMock />,
  },
  {
    label: "AI Sales Assistant",
    eyebrow: "AI sales assistant",
    title: "An assistant that knows your live deals.",
    body: "Grounded in your pipeline, your offer, and how the best sellers actually work, not generic AI. Ask how to open a CTO or unstick a cold deal and get an answer tied to the actual account.",
    mock: <CoachMock />,
  },
  {
    label: "Pipeline CRM",
    eyebrow: "Solo-founder CRM",
    title: "Every deal on one board.",
    body: "A lightweight pipeline built for outbound: new, contacted, replied, won. No enterprise CRM bloat, just the columns a founder actually moves cards between.",
    mock: <PipelineMock />,
  },
  {
    label: "Inbox",
    eyebrow: "Unified inbox",
    title: "Replies land here, and the sequence stops.",
    body: "Every thread in one place, with reply detection wired in. The moment someone answers, remaining follow-ups cancel automatically. No one ever gets a “just following up” after they replied.",
    mock: <InboxMock />,
  },
  {
    label: "Automation",
    eyebrow: "Follow-up automation",
    title: "Follow-ups that run themselves.",
    body: "Approve once and Saqua runs a multi-touch cadence over real days, each touch a new angle, with timing re-anchored to the actual send so it never compresses into something that looks automated.",
    mock: <AutomationMock />,
  },
];

/* ── Component ──────────────────────────────────────────────────────── */
export function FeatureTabs() {
  const [active, setActive] = useState(0);
  const hovering = useRef(false);

  useEffect(() => {
    const t = setInterval(() => {
      if (!hovering.current) setActive((a) => (a + 1) % TABS.length);
    }, 4500);
    return () => clearInterval(t);
  }, []);

  const tab = TABS[active];

  return (
    <div
      onMouseEnter={() => (hovering.current = true)}
      onMouseLeave={() => (hovering.current = false)}
    >
      {/* Tab bar — segmented pill with an animated gradient indicator */}
      <div className="mx-auto flex max-w-full flex-wrap justify-center gap-1 overflow-x-auto rounded-full border border-border bg-card p-1 shadow-sm">
        {TABS.map((t, i) => (
          <button
            key={t.label}
            onClick={() => setActive(i)}
            className={cn(
              "whitespace-nowrap rounded-full px-4 py-2 text-sm font-medium transition-colors",
              i === active
                ? "bg-grad-brand text-white shadow-[0_6px_16px_rgba(79,90,247,.28)]"
                : "text-muted hover:text-text",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Active panel */}
      <div
        key={active}
        className="mt-10 grid animate-fade-up items-center gap-10 text-left lg:grid-cols-2"
      >
        <div>
          <span className="text-sm font-semibold text-accent">{tab.eyebrow}</span>
          <h3 className="mt-3 font-display text-2xl font-semibold tracking-tight md:text-3xl">{tab.title}</h3>
          <p className="mt-4 text-base leading-7 text-muted">{tab.body}</p>
        </div>
        <div
          className="rounded-2xl p-6 md:p-10"
          style={{
            background:
              "radial-gradient(70% 70% at 55% 45%, rgba(122,110,247,.18), rgba(122,110,247,.05) 60%, transparent 78%)",
          }}
        >
          {tab.mock}
        </div>
      </div>
    </div>
  );
}
