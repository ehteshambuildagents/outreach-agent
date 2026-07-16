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
  Target,
  type LucideIcon,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  draft_email: { label: "Draft email", icon: Mail },
  draft_x_reply: { label: "X reply", icon: MessageSquare },
  draft_reddit_comment: { label: "Reddit", icon: MessageCircle },
  draft_hn_reply: { label: "HN reply", icon: Hash },
  draft_contact_form: { label: "Contact form", icon: FileText },
};

function scoreTone(score: number): Tone {
  if (score >= 70) return "success";
  if (score >= 45) return "accent";
  if (score >= 25) return "warn";
  return "neutral";
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
  return (
    <div className="space-y-2.5">
      {summary && (
        <div className="flex items-center gap-2 px-1 text-xs text-muted">
          <Target className="size-3.5 text-accent-hi" />
          <span>
            Researched {summary.researched ?? prospects.filter((p) => p.status === "ok").length} of{" "}
            {summary.total ?? prospects.length}
            {summary.top ? ` · top: ${summary.top}` : ""}
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
  const rec = REC_META[p.recommendation] ?? { label: p.recommendation || p.status, tone: "neutral" as Tone };
  const detail = p.detail || {};
  const findings = detail.findings || [];
  const sources = detail.sources || [];
  const actions = (p.actions || []).filter((a) => ACTION_META[a]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, delay: Math.min(index * 0.03, 0.24) }}
    >
      <Card className="overflow-hidden">
        {/* Collapsed header: company + score + one-paragraph preview. */}
        <div className="px-5 py-4">
          <button
            onClick={() => researched && setOpen((v) => !v)}
            className={cn("flex w-full items-start gap-3 text-left", researched && "cursor-pointer")}
            aria-expanded={open}
            disabled={!researched}
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="truncate text-sm font-medium text-text">{p.company}</span>
                {researched && (
                  <Badge tone={scoreTone(p.score)}>Fit {p.score}/100</Badge>
                )}
                <Badge tone={rec.tone} dot>
                  {rec.label}
                </Badge>
              </div>
              <p className="mt-1.5 text-xs leading-5 text-text-2">{p.preview}</p>
            </div>
            {researched && (
              <ChevronDown
                className={cn("mt-0.5 size-4 shrink-0 text-muted transition-transform", open && "rotate-180")}
              />
            )}
          </button>

          {/* Per-prospect actions (draft email / channel). Nothing sends or posts. */}
          {researched && actions.length > 0 && onAction && (
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
          {open && researched && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden border-t border-border-faint"
            >
              <div className="space-y-4 px-5 py-4">
                <Section label="Why this score">
                  <p className="text-xs leading-5 text-text-2">{p.score_reason || "—"}</p>
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

                {findings.length > 0 && (
                  <Section label={`Findings (${findings.length})`}>
                    <ul className="space-y-2">
                      {findings.map((f, k) => (
                        <li key={k} className="rounded-md border border-border-faint bg-white/[0.02] p-2.5">
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
                              className="mt-1 inline-flex items-center gap-1 text-[11px] text-accent-hi hover:underline"
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
                          className="inline-flex items-center gap-1 rounded-sm border border-border-faint bg-white/[0.02] px-2 py-1 text-[11px] text-text-2 hover:border-border-strong hover:text-text"
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
