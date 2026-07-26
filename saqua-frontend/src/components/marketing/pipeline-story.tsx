"use client";

import { useRef } from "react";
import type { LucideIcon } from "lucide-react";
import {
  motion,
  useReducedMotion,
  useScroll,
  useSpring,
  type Variants,
} from "framer-motion";
import { Check, PenLine, Radar, Search, Send, ShieldCheck } from "lucide-react";
import { CountUp } from "@/components/ui/count-up";
import { cn } from "@/lib/utils";

/**
 * How it works: the four steps, in order, as a scroll-driven trail.
 *
 * This is the page's explainer, and the animation is the explanation. A rail
 * fills as you scroll so the sequence is felt rather than read, and each step
 * carries the artifact it produces: the scored list, the facts with their
 * sources, the draft, the pre-send check. One company (Linear) runs through all
 * four, so the section reads as one story instead of four screenshots.
 *
 * Deliberately NOT the app chrome used by <FeatureTabs />. Those mocks are a
 * tour of the workspace; these are the trail of evidence a single prospect
 * leaves behind, so they stay small and unwindowed to keep the registers apart.
 *
 * Every motion here degrades to the finished state under prefers-reduced-motion:
 * the rail renders full, and staggered children start already shown.
 */

const EASE: [number, number, number, number] = [0.22, 0.61, 0.36, 1];

const list: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.13, delayChildren: 0.18 } },
};

const item: Variants = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0, transition: { duration: 0.42, ease: EASE } },
};

/** Parent of a staggered group. Children reveal in DOM order, once, in view. */
function Stagger({ children, className }: { children: React.ReactNode; className?: string }) {
  const reduced = useReducedMotion();
  return (
    <motion.div
      className={className}
      variants={list}
      initial={reduced ? "show" : "hidden"}
      whileInView="show"
      viewport={{ once: true, margin: "-60px" }}
    >
      {children}
    </motion.div>
  );
}

function Item({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <motion.div variants={item} className={className}>
      {children}
    </motion.div>
  );
}

/* ── Artifact shell ────────────────────────────────────────────────────
   A small evidence card with a mono caption instead of a title bar, so it
   reads as output rather than a second app window. */
function Artifact({ caption, children }: { caption: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border-faint bg-card/80 p-3 shadow-card">
      <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-faint">
        {caption}
      </div>
      {children}
    </div>
  );
}

function Line({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md border border-border-faint bg-black/[0.02] px-2.5 py-1.5 text-[11px] leading-4 text-text-2",
        className,
      )}
    >
      {children}
    </div>
  );
}

/* ── Step 1: the scored list populates ────────────────────────────────── */
const MATCHES: [string, string, number][] = [
  ["Linear", "linear.app", 92],
  ["Vercel", "vercel.com", 88],
  ["Resend", "resend.com", 85],
];

const FindArtifact = () => (
  <Artifact caption="prospects matched">
    <Stagger className="space-y-1.5">
      {MATCHES.map(([name, domain, fit]) => (
        <Item key={name}>
          <div className="flex items-center justify-between rounded-md border border-border-faint bg-black/[0.02] px-2.5 py-1.5">
            <div>
              <div className="text-[11px] font-medium leading-4 text-text">{name}</div>
              <div className="font-mono text-[10px] leading-4 text-muted">{domain}</div>
            </div>
            <span className="font-mono text-[11px] font-semibold tabular-nums text-accent">
              {fit}
            </span>
          </div>
        </Item>
      ))}
    </Stagger>
  </Artifact>
);

/* ── Step 2: facts land, then the score settles ───────────────────────── */
const ResearchArtifact = () => (
  <Artifact caption="verified facts">
    <Stagger className="space-y-1.5">
      <Item>
        <Line>
          <span className="text-accent">&ldquo;</span> Posted 2 GTM roles in the last 30 days
        </Line>
      </Item>
      <Item>
        <Line>
          <span className="text-accent">&ldquo;</span> Series B, 8 months ago
        </Line>
      </Item>
      <Item>
        <div className="flex items-center justify-between border-t border-border-faint pt-2">
          <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-accent">
            <Check className="size-3" /> Qualified
          </span>
          <span className="font-mono text-[10px] text-muted">
            <CountUp to={92} className="text-accent" /> fit · 3 sources
          </span>
        </div>
      </Item>
    </Stagger>
  </Artifact>
);

/* ── Step 3: the draft appears, line by line ──────────────────────────
   font-serif is reserved system-wide for Saqua's own generated email copy. */
const WriteArtifact = () => (
  <Artifact caption="draft, in your voice">
    <Stagger className="space-y-2">
      <Item>
        <div className="text-[11px] font-medium text-text">Two GTM hires in 30 days</div>
      </Item>
      <Item>
        <p className="font-serif text-[12px] leading-5 text-text-2">
          You&apos;ve posted two GTM roles this month, eight months after the Series B.
        </p>
      </Item>
      <Item>
        <p className="font-serif text-[12px] leading-5 text-text-2">
          Most founders hire there because outbound never became repeatable. That is the
          part we fixed.
        </p>
      </Item>
      <Item>
        <p className="font-serif text-[12px] leading-5 text-text-2">Worth fifteen minutes?</p>
      </Item>
    </Stagger>
  </Artifact>
);

/* ── Step 4: the check clears, the cadence runs, the reply stops it ───── */
const SendArtifact = () => (
  <Artifact caption="before it leaves">
    <Stagger className="space-y-1.5">
      <Item>
        <Line className="border-accent-line bg-accent-soft text-accent">
          <ShieldCheck className="size-3 shrink-0" /> Deliverability and spend checked
        </Line>
      </Item>
      <Item>
        <Line>
          <span className="flex-1">Day 3 follow-up, new angle</span>
          <span className="font-mono text-[10px] text-muted">queued</span>
        </Line>
      </Item>
      <Item>
        <Line className="border-[color:var(--success-soft)] bg-[color:var(--success-soft)] text-[color:var(--success)]">
          <Check className="size-3 shrink-0" /> Reply detected, cadence stopped
        </Line>
      </Item>
    </Stagger>
  </Artifact>
);

/* ── Steps ────────────────────────────────────────────────────────────── */
const STEPS: {
  label: string;
  icon: LucideIcon;
  title: string;
  body: string;
  artifact: React.ReactNode;
}[] = [
  {
    label: "Find",
    icon: Radar,
    title: "You describe who you sell to.",
    body: "One sentence is enough. Saqua goes looking for companies that match it and scores each one, so you are never handed a list of five hundred maybes to sort through yourself.",
    artifact: <FindArtifact />,
  },
  {
    label: "Research",
    icon: Search,
    title: "It reads the company before it writes.",
    body: "The real site, the funding news, the open roles. Saqua keeps only the facts it can point at, with the quote and the source behind each one, and scores how well the company actually fits.",
    artifact: <ResearchArtifact />,
  },
  {
    label: "Write",
    icon: PenLine,
    title: "Then it writes the opener around one real detail.",
    body: "Short, specific, and in your voice. The first line names something true about them, not about you. Anything the research could not prove never makes it into the email.",
    artifact: <WriteArtifact />,
  },
  {
    label: "Send",
    icon: Send,
    title: "You say yes. It handles the rest.",
    body: "Every draft clears a deliverability and spend check before it can leave. After your approval, follow-ups go out over real days, each one taking a new angle, and the whole cadence calls itself off as soon as someone answers.",
    artifact: <SendArtifact />,
  },
];

/* ── Component ────────────────────────────────────────────────────────── */
export function PipelineStory() {
  const ref = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();
  // The rail tracks the section through the viewport: full by the time the last
  // step is read, not at the very bottom of the element.
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ["start 78%", "end 62%"],
  });
  const fill = useSpring(scrollYProgress, { stiffness: 110, damping: 30, restDelta: 0.001 });

  return (
    <div ref={ref} className="relative mx-auto max-w-4xl">
      {/* The rail. Sits behind the numbered nodes and fills as you scroll. */}
      <div
        aria-hidden
        className="absolute bottom-10 left-[17px] top-3 w-px bg-border"
      >
        {reduced ? (
          <div className="h-full w-px bg-grad-brand" />
        ) : (
          <motion.div
            className="h-full w-px origin-top bg-grad-brand"
            style={{ scaleY: fill }}
          />
        )}
      </div>

      {STEPS.map((s, i) => (
        <motion.div
          key={s.label}
          initial={reduced ? false : { opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-90px" }}
          transition={{ duration: 0.55, ease: EASE }}
          className={cn(
            "relative grid gap-5 pl-14 md:grid-cols-[1fr_minmax(0,300px)] md:items-start md:gap-10",
            i < STEPS.length - 1 ? "pb-14" : "pb-2",
          )}
        >
          {/* Numbered node, sitting on the rail. */}
          <span className="absolute left-0 top-0 grid size-[35px] place-items-center rounded-full border border-border bg-card text-accent shadow-card">
            <s.icon className="size-4" />
          </span>

          <div>
            <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-accent">
              {String(i + 1).padStart(2, "0")} {s.label}
            </span>
            <h3 className="mt-2 font-display text-xl font-semibold tracking-tight text-text md:text-2xl">
              {s.title}
            </h3>
            <p className="mt-3 max-w-prose text-sm leading-6 text-text-2">{s.body}</p>
          </div>

          <div>{s.artifact}</div>
        </motion.div>
      ))}
    </div>
  );
}
