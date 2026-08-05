"use client";

import { useState } from "react";
import { Search, Target, Sparkles, PenLine, ShieldCheck, ChevronDown, UserPlus } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { CampaignPreviewProspect } from "@/lib/api";
import { ResearchTrail } from "@/components/chat/research-trail";

export const FINAL_STATUS: Record<string, { label: string; tone: "success" | "warn" | "danger" | "neutral" | "accent" }> = {
  sendable: { label: "Ready to send", tone: "success" },
  route_only: { label: "Route only", tone: "warn" },
  guard_blocked: { label: "Guard blocked", tone: "danger" },
  qualification_blocked: { label: "Not qualified", tone: "neutral" },
  strategy_hold: { label: "On hold", tone: "warn" },
  writer_failed: { label: "Writer failed", tone: "danger" },
  research_failed: { label: "Research failed", tone: "neutral" },
  // Not a failure of the company or of us: the run hit its time budget while
  // this one was still being read, so it was left out rather than rushed.
  timed_out: { label: "Ran out of time", tone: "warn" },
  error: { label: "Error", tone: "danger" },
};

/** One prospect row across the full pipeline (research → qualification → strategy
 * → writer → guard), expandable to show the generated email. Shared by the New
 * Campaign flow and Campaign Detail so both render real backend data identically. */
export function ProspectCard({
  prospect: p,
  index = 0,
  onAddRecipient,
}: {
  prospect: CampaignPreviewProspect;
  index?: number;
  onAddRecipient?: (prospect: CampaignPreviewProspect) => void;
}) {
  const [open, setOpen] = useState(false);
  const status = FINAL_STATUS[p.final_status] ?? { label: p.final_status, tone: "neutral" as const };
  const email = p.email;
  const hasEmail = email?.status === "ok" && email.body;
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: Math.min(index * 0.04, 0.3) }}
    >
      <Card className="overflow-hidden">
        <div className="flex w-full items-center gap-3 px-5 py-4 transition-colors hover:bg-black/[0.02]">
          <button onClick={() => setOpen((v) => !v)} className="flex min-w-0 flex-1 items-center gap-4 text-left">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm font-medium text-text">
                  {p.company_name || p.research?.company_name || p.domain}
                </span>
                <Badge tone={status.tone} dot>
                  {status.label}
                </Badge>
                {p.cadence_warning && (
                  <Badge tone="warn" title={p.cadence_warning.message}>
                    Partial cadence · {p.cadence_warning.planned_steps} steps
                  </Badge>
                )}
              </div>
              <div className="mt-0.5 truncate text-xs text-muted">{p.research?.summary || p.reason || p.domain}</div>
            </div>
          </button>
          <div className="hidden shrink-0 items-center gap-2 sm:flex">
            {p.qualification && <MiniStat label="Fit" value={`${p.qualification.qualification_score}`} />}
            {p.guard && (
              <Badge tone={p.guard.decision === "ALLOW" ? "success" : p.guard.decision === "WARN" ? "warn" : "danger"}>
                Guard {p.guard.decision}
              </Badge>
            )}
          </div>
          {p.final_status === "route_only" && onAddRecipient && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onAddRecipient(p)}
              aria-label={`Add recipient for ${p.company_name || p.domain}`}
            >
              <UserPlus className="size-3.5" /> Add recipient
            </Button>
          )}
          <button onClick={() => setOpen((v) => !v)} aria-label="Toggle prospect details">
            <ChevronDown className={cn("size-4 shrink-0 text-muted transition-transform", open && "rotate-180")} />
          </button>
        </div>

        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden border-t border-border-faint"
            >
              {p.cadence_warning && (
                <div className="mx-5 mt-4 flex items-start gap-2 rounded-md border border-warn/30 bg-warn/[0.06] px-3 py-2 text-xs text-text-2">
                  <span className="mt-0.5 font-medium text-warn">Partial cadence</span>
                  <span>{p.cadence_warning.message}</span>
                </div>
              )}
              <div className="grid gap-4 px-5 py-4 lg:grid-cols-2">
                <div className="space-y-3">
                  <AgentRow icon={Search} label="Research">
                    {p.research?.status === "ok" ? (
                      <>
                        Score {p.research.research_score ?? "-"} · {p.research.pages_crawled.length} pages
                        {p.research.named_person ? ` · ${p.research.named_person}` : ""}
                      </>
                    ) : (
                      <span className="text-muted">{p.research?.reason || "Not researched"}</span>
                    )}
                  </AgentRow>
                  <AgentRow icon={Target} label="Qualification">
                    {p.qualification ? (
                      <>
                        {p.qualification.recommendation} · score {p.qualification.qualification_score}
                      </>
                    ) : (
                      <span className="text-muted">-</span>
                    )}
                  </AgentRow>
                  <AgentRow icon={Sparkles} label="Strategy">
                    {p.strategy ? (
                      <>
                        {p.strategy.recommended_action}
                        {p.strategy.primary_hook ? ` · “${p.strategy.primary_hook}”` : ""}
                      </>
                    ) : (
                      <span className="text-muted">-</span>
                    )}
                  </AgentRow>
                  <AgentRow icon={ShieldCheck} label="Guard">
                    {p.guard ? (
                      <>
                        {p.guard.decision} · risk {p.guard.overallRisk}/100
                      </>
                    ) : (
                      <span className="text-muted">-</span>
                    )}
                  </AgentRow>
                </div>

                <div className="rounded-md border border-border-faint bg-black/[0.02] p-4">
                  {hasEmail ? (
                    <>
                      <div className="mb-1 text-[11px] uppercase tracking-wide text-muted">
                        To {email?.to || email?.recipient?.email || email?.recipient?.route || "-"}
                      </div>
                      <div className="text-sm font-medium text-text">{email?.subject}</div>
                      <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-text-2">{email?.body}</p>
                    </>
                  ) : (
                    <div className="flex h-full items-center gap-2 text-xs text-muted">
                      <PenLine className="size-4" />
                      {email?.reason || p.reason || "No email generated for this prospect."}
                    </div>
                  )}
                </div>
              </div>

              {/* The honest, evidence-backed research trail for this prospect, derived
                  from real persisted stage outcomes. Renders nothing when there is no
                  trail, so it never adds an empty card. `live` is false: this is
                  restored data, never replayed as if it were happening now. */}
              {p.research_trail && p.research_trail.length > 0 && (
                <div className="px-5 pb-4">
                  <ResearchTrail events={p.research_trail} live={false} />
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </Card>
    </motion.div>
  );
}

function AgentRow({ icon: Icon, label, children }: { icon: typeof Search; label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 text-xs">
      <Icon className="mt-0.5 size-3.5 shrink-0 text-accent" />
      <div>
        <span className="text-muted">{label}: </span>
        <span className="text-text-2">{children}</span>
      </div>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-sm border border-border-faint bg-black/[0.02] px-2 py-1 text-center">
      <div className="text-[9px] uppercase text-muted">{label}</div>
      <div className="text-xs font-semibold text-text">{value}</div>
    </div>
  );
}
