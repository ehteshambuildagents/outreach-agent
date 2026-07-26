"use client";

import { useState } from "react";
import {
  ChevronDown,
  Mail,
  MessageSquare,
  MessageCircle,
  Hash,
  FileText,
  Quote,
  Link2,
  Search,
  Target,
  Check,
  Minus,
  type LucideIcon,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CountUp } from "@/components/ui/count-up";
import { cn } from "@/lib/utils";
import type { ProspectEntry, ProspectsCardData } from "@/lib/api";

type Tone = "success" | "warn" | "danger" | "neutral" | "accent";

const REC_META: Record<string, { label: string; tone: Tone }> = {
  high_priority: { label: "High priority", tone: "success" },
  continue: { label: "Worth pursuing", tone: "accent" },
  research_more: { label: "Research more", tone: "warn" },
  reject: { label: "Skip", tone: "neutral" },
};

const ACTION_META: Record<string, { label: string; icon: LucideIcon }> = {
  research_prospect: { label: "Research this", icon: Search },
  draft_email: { label: "Draft email", icon: Mail },
  draft_x_reply: { label: "X reply", icon: MessageSquare },
  draft_reddit_comment: { label: "Reddit", icon: MessageCircle },
  draft_hn_reply: { label: "HN reply", icon: Hash },
  draft_contact_form: { label: "Contact form", icon: FileText },
};

/** How strongly the hiring signal was established. "role" is the only one that
 *  means "hiring for the thing you asked about"; the others are shown but must
 *  not be dressed up as more than they are. */
const HIRING_TONE: Record<string, Tone> = {
  role: "success",
  any: "neutral",
  page: "neutral",
};

function scoreTone(score: number): Tone {
  if (score >= 70) return "success";
  if (score >= 45) return "accent";
  if (score >= 25) return "warn";
  return "neutral";
}

/** The plain-language case for a discovered company's rank, derived from the
 *  customer-likelihood factor breakdown plus the verified signals. This is the
 *  "Why is this ranked here?" the user should be able to read at a glance, rather
 *  than a bare number. Ordered strongest-first and capped so the card stays light. */
function whyRanked(p: ProspectEntry): string[] {
  const d = p.detail || {};
  const s = d.score_breakdown || {};
  const out: string[] = [];
  if (d.hiring?.match === "role") out.push("Verified hiring this role");
  else if (d.hiring?.verified) out.push("Actively hiring right now");
  const g = d.growth?.headcount_6mo;
  if (typeof g === "number" && g >= 0.05) out.push(`Growing headcount, up ${Math.round(g * 100)}% in 6 months`);
  if ((s.icp_match ?? 0) >= 0.6 || d.industry_kind === "software") out.push("Strong ICP match, B2B software");
  if ((s.buying_signal ?? 0) >= 0.35) out.push("Recent buying signals");
  if (!d.is_public && (s.founder_access ?? 0) >= 0.55) out.push("Founder still reachable directly");
  if ((s.corroboration ?? 0) >= 0.7) out.push("Confirmed across multiple sources");
  return out.slice(0, 4);
}

/** What holds a company BACK, from the same real state. Without this a card can
 *  show four green checks next to a 28% score, which reads as broken. The score
 *  is a customer-likelihood judgement, so the reason it was marked down has to be
 *  as visible as the reasons it was marked up. */
function whyNot(p: ProspectEntry): string[] {
  const d = p.detail || {};
  const s = d.score_breakdown || {};
  const out: string[] = [];
  if (d.is_public) out.push("Large public company, expect procurement over a founder");
  else if ((d.annual_revenue ?? 0) >= 500_000_000)
    out.push("Very large company, a founder is unlikely to be the buyer");
  if (d.tier && d.tier !== "company") out.push("A job board or marketplace, not the employer");
  if (d.hiring?.verified && d.hiring.match !== "role") out.push("Hiring, but not for this role");
  if (!d.hiring?.verified) out.push("No live posting confirmed yet");
  if ((s.buying_signal ?? 0) < 0.2) out.push("Little sign they are in market right now");
  return out.slice(0, 2);
}

/** The scored, browsable prospect list returned by `research_prospects`. Each row
 * is a collapsed one-paragraph preview (headline finding + fit score) that expands
 * to the full research trail — findings with source + confidence, the score
 * reasoning, and sources. Never both states at once. Action buttons under each
 * preview trigger the writer / channel tools for that company. */
export function ProspectsCard({
  data,
  onAction,
  busy = false,
}: {
  data: ProspectsCardData;
  onAction?: (action: string, prospect: ProspectEntry) => void;
  busy?: boolean;
}) {
  const prospects = data.prospects || [];
  const summary = data.summary;
  // Discovery and research produce the same card; the header has to describe
  // whichever one this is, and not claim research that did not happen.
  const anyDiscovered = prospects.some((p) => p.status === "discovered");
  return (
    <div className="space-y-2.5">
      {summary && (
        <div className="flex items-center gap-2 px-1 text-xs text-muted">
          <Target className="size-3.5 text-accent" />
          <span>
            {anyDiscovered ? (
              <>
                Found {summary.total ?? prospects.length}
                {summary.considered ? ` from ${summary.considered} considered` : ""}
                {summary.demoted ? ` · ${summary.demoted} aggregators filtered out` : ""}
                {summary.top ? ` · best: ${summary.top}` : ""}
              </>
            ) : (
              <>
                Researched {summary.researched ?? prospects.filter((p) => p.status === "ok").length} of{" "}
                {summary.total ?? prospects.length}
                {summary.top ? ` · top: ${summary.top}` : ""}
              </>
            )}
          </span>
        </div>
      )}
      {prospects.map((p, i) => (
        <ProspectRow key={`${p.company}-${i}`} prospect={p} index={i} onAction={onAction} busy={busy} />
      ))}
    </div>
  );
}

function ProspectRow({
  prospect: p,
  index,
  onAction,
  busy,
}: {
  prospect: ProspectEntry;
  index: number;
  onAction?: (action: string, prospect: ProspectEntry) => void;
  busy?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const researched = p.status === "ok";
  // A DISCOVERED lead has no fit score yet, only a match confidence. It still
  // expands, because the evidence behind the match is the point of the card.
  const discovered = p.status === "discovered";
  const expandable = researched || discovered;
  const rec = REC_META[p.recommendation] ?? { label: p.recommendation || p.status, tone: "neutral" as Tone };
  const detail = p.detail || {};
  const findings = detail.findings || [];
  const sources = detail.sources || [];
  const reasons = detail.match_reasons || [];
  const hiring = detail.hiring || null;
  const growth = detail.growth?.headcount_6mo ?? null;
  const isFallback = discovered && detail.tier && detail.tier !== "company";
  const actions = (p.actions || []).filter((a) => ACTION_META[a]);

  const why = discovered ? whyRanked(p) : [];
  const caveats = discovered ? whyNot(p) : [];

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      // A pronounced per-card stagger so the list REVEALS itself one company at a
      // time (Linear/ChatGPT), rather than snapping in as a finished block.
      transition={{ duration: 0.3, delay: Math.min(index * 0.07, 0.5), ease: [0.22, 0.61, 0.36, 1] }}
    >
      <Card className="overflow-hidden">
        {/* Collapsed header: company + score + one-paragraph preview. */}
        <div className="px-5 py-4">
          <button
            onClick={() => expandable && setOpen((v) => !v)}
            className={cn("flex w-full items-start gap-3 text-left", expandable && "cursor-pointer")}
            aria-expanded={open}
            disabled={!expandable}
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="truncate text-sm font-medium text-text">{p.company}</span>
                {researched && (
                  <Badge tone={scoreTone(p.score)}>
                    Fit <CountUp to={p.score} />/100
                  </Badge>
                )}
                {discovered && (
                  <Badge tone={scoreTone(p.score)}>
                    <CountUp to={p.score} />% match
                  </Badge>
                )}
                {!discovered && (
                  <Badge tone={rec.tone} dot>
                    {rec.label}
                  </Badge>
                )}
                {isFallback && <Badge tone="warn">Fallback: {detail.kind?.replace(/_/g, " ")}</Badge>}
                {hiring?.summary && (
                  <Badge tone={HIRING_TONE[hiring.match || "page"] ?? "neutral"} dot>
                    {hiring.match === "role" ? "Hiring for this role" : "Hiring"}
                  </Badge>
                )}
              </div>
              {/* The website, which a lead is useless without. */}
              {p.website && (
                <span className="mt-0.5 block truncate font-mono text-[11px] text-muted">
                  {hostOf(p.website)}
                </span>
              )}
              <p className="mt-1.5 text-xs leading-5 text-text-2">{p.preview}</p>
            </div>
            {expandable && (
              <ChevronDown
                className={cn("mt-0.5 size-4 shrink-0 text-muted transition-transform", open && "rotate-180")}
              />
            )}
          </button>

          {/* Why this ranked here — the case at a glance, always visible on a
              discovered lead so the number is never unexplained. */}
          {discovered && (why.length > 0 || caveats.length > 0) && (
            <div className="mt-3">
              <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
                Why this ranked #{index + 1}
              </div>
              <ul className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
                {why.map((r, k) => (
                  <motion.li
                    key={r}
                    initial={{ opacity: 0, x: -4 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.22, delay: Math.min(index * 0.07, 0.5) + 0.12 + k * 0.06 }}
                    className="flex items-center gap-1.5 text-xs leading-5 text-text-2"
                  >
                    <Check className="size-3.5 shrink-0 text-accent" />
                    <span className="min-w-0">{r}</span>
                  </motion.li>
                ))}
                {/* What marked it DOWN, so a low score next to green checks is
                    explained rather than looking like a bug. */}
                {caveats.map((r, k) => (
                  <motion.li
                    key={r}
                    initial={{ opacity: 0, x: -4 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{
                      duration: 0.22,
                      delay: Math.min(index * 0.07, 0.5) + 0.12 + (why.length + k) * 0.06,
                    }}
                    className="flex items-center gap-1.5 text-xs leading-5 text-muted"
                  >
                    <Minus className="size-3.5 shrink-0 text-muted" />
                    <span className="min-w-0">{r}</span>
                  </motion.li>
                ))}
              </ul>
            </div>
          )}

          {/* Per-prospect actions (research / draft). Nothing sends or posts. */}
          {expandable && actions.length > 0 && onAction && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {actions.map((a, idx) => {
                const meta = ACTION_META[a];
                const Icon = meta.icon;
                return (
                  <Button
                    key={a}
                    size="sm"
                    variant={idx === 0 ? "secondary" : "ghost"}
                    disabled={busy}
                    onClick={() => onAction(a, p)}
                  >
                    <Icon className="size-3.5" /> {meta.label}
                  </Button>
                );
              })}
            </div>
          )}
        </div>

        {/* Expanded detail: findings w/ source + confidence, score reasoning, sources. */}
        <AnimatePresence initial={false}>
          {open && expandable && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden border-t border-border-faint"
            >
              <div className="space-y-4 px-5 py-4">
                {/* DISCOVERED: the itemised case for picking this company, plus the
                    hiring evidence with its dates. Nothing here is researched yet,
                    and the panel says so rather than implying otherwise. */}
                {discovered && reasons.length > 0 && (
                  <Section label="Why it matched">
                    <ul className="space-y-1">
                      {reasons.map((r, k) => (
                        <li key={k} className="flex gap-1.5 text-xs leading-5 text-text-2">
                          <span className="mt-1.5 size-1 shrink-0 rounded-full bg-accent" />
                          <span className="min-w-0">{r}</span>
                        </li>
                      ))}
                    </ul>
                  </Section>
                )}

                {discovered && hiring?.summary && (
                  <Section label="Hiring signal">
                    <p className="text-xs leading-5 text-text-2">{hiring.summary}</p>
                    {hiring.match !== "role" && (
                      <p className="mt-1 text-[11px] leading-4 text-muted">
                        Not a match for the role you asked about.
                      </p>
                    )}
                    {(hiring.postings?.length ?? 0) > 0 && (
                      <ul className="mt-2 space-y-1">
                        {hiring.postings!.slice(0, 3).map((j, k) => (
                          <li key={k} className="text-[11px] leading-4 text-muted">
                            {j.url ? (
                              <a href={j.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                                {j.title || j.url}
                              </a>
                            ) : (
                              j.title
                            )}
                            {j.posted_at ? ` · posted ${String(j.posted_at).slice(0, 10)}` : ""}
                          </li>
                        ))}
                      </ul>
                    )}
                  </Section>
                )}

                {discovered && typeof growth === "number" && growth !== 0 && (
                  <Section label="Headcount">
                    <p className="text-xs leading-5 text-text-2">
                      {growth > 0 ? "Up" : "Down"} {Math.abs(Math.round(growth * 100))}% over six months
                    </p>
                  </Section>
                )}

                {discovered && (
                  <p className="text-[11px] leading-4 text-muted">
                    Not researched yet, so there is no fit score. Research it to get the
                    verified facts and a score.
                  </p>
                )}

                {researched && (
                  <Section label="Why this score">
                    <p className="text-xs leading-5 text-text-2">{p.score_reason || "-"}</p>
                    {(detail.strongest_signals?.length ?? 0) > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {detail.strongest_signals!.map((s, k) => (
                          <Badge key={k} tone="neutral">
                            {s}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </Section>
                )}

                {findings.length > 0 && (
                  <Section label={`Findings (${findings.length})`}>
                    <ul className="space-y-2">
                      {findings.map((f, k) => (
                        <li key={k} className="rounded-md border border-border-faint bg-black/[0.02] p-2.5">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <span className="text-[11px] uppercase tracking-wide text-muted">{f.label}</span>
                              <p className="text-xs leading-5 text-text-2">{f.value}</p>
                            </div>
                            {typeof f.confidence === "number" && (
                              <Badge tone={f.confidence >= 0.66 ? "success" : f.confidence >= 0.4 ? "warn" : "neutral"}>
                                {Math.round(f.confidence * 100)}%
                              </Badge>
                            )}
                          </div>
                          {f.quote && (
                            <p className="mt-1.5 flex gap-1.5 text-[11px] italic leading-4 text-muted">
                              <Quote className="mt-0.5 size-3 shrink-0" />
                              <span className="min-w-0">{f.quote}</span>
                            </p>
                          )}
                          {f.source && (
                            <a
                              href={f.source}
                              target="_blank"
                              rel="noreferrer"
                              className="mt-1 inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
                            >
                              <Link2 className="size-3" />
                              {sourceHost(f.source)}
                            </a>
                          )}
                        </li>
                      ))}
                    </ul>
                  </Section>
                )}

                {(detail.missing_information?.length ?? 0) > 0 && (
                  <Section label="Missing information">
                    <ul className="list-disc space-y-0.5 pl-4 text-xs leading-5 text-muted">
                      {detail.missing_information!.map((m, k) => (
                        <li key={k}>{m}</li>
                      ))}
                    </ul>
                  </Section>
                )}

                {sources.length > 0 && (
                  <Section label={`Sources (${sources.length})`}>
                    <div className="flex flex-wrap gap-1.5">
                      {sources.map((s, k) => (
                        <a
                          key={k}
                          href={s.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 rounded-sm border border-border-faint bg-black/[0.02] px-2 py-1 text-[11px] text-text-2 hover:border-border-strong hover:text-text"
                        >
                          <Link2 className="size-3 text-muted" />
                          {s.domain}
                        </a>
                      ))}
                    </div>
                  </Section>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>
    </motion.div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">{label}</div>
      {children}
    </div>
  );
}

function sourceHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** The bare host for display under a company name. Falls back to the raw string
 *  so a malformed website still shows something rather than vanishing. */
function hostOf(url: string): string {
  try {
    return new URL(url.includes("://") ? url : `https://${url}`).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
