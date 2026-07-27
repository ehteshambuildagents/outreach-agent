"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Megaphone,
  Plus,
  RefreshCw,
  ChevronDown,
  Rocket,
  Ban,
  Clock,
  MailCheck,
  ArrowRight,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { StatusPill } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { ProspectCard } from "@/components/campaign/prospect-card";
import { api, type CampaignDetail, type WorkflowStatus } from "@/lib/api";
import { cn, fmtCurrency, timeAgo } from "@/lib/utils";

type Bucket = "launched" | "ready" | "blocked";

const BUCKET_META: Record<Bucket, { label: string; dot: string; hint: string }> = {
  launched: { label: "Launched", dot: "bg-accent", hint: "Live sequences, sending and following up." },
  ready: { label: "Ready", dot: "bg-text-2", hint: "Previewed and ready to launch." },
  blocked: { label: "Blocked", dot: "bg-danger", hint: "Nothing reachable. Needs attention." },
};

function bucketOf(c: CampaignDetail): Bucket {
  const s = (c.status || "").toLowerCase();
  const launchable = c.summary?.launchable ?? 0;
  const discovered = c.summary?.discovered ?? 0;
  const launched = ["launched", "active", "running", "paused", "completed"].includes(s) || c.workflow_ids.length > 0;
  if (launched) return "launched";
  if (discovered > 0 && launchable === 0) return "blocked";
  if (["blocked", "failed", "error"].includes(s)) return "blocked";
  return "ready";
}

type State =
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "loaded"; campaigns: CampaignDetail[] };

export default function CampaignsPage() {
  const [state, setState] = useState<State>({ status: "loading" });
  const [details, setDetails] = useState<Record<string, CampaignDetail>>({});

  function load() {
    setState({ status: "loading" });
    setDetails({});
    void api.campaigns().then((res) => {
      if (!res.ok) {
        setState({ status: "error", error: res.error });
        return;
      }
      setState({ status: "loaded", campaigns: res.data.campaigns });
      // Enrich with full detail (prospects + live workflows) for stats + expand.
      res.data.campaigns.forEach((c) => {
        void api.campaign(c.id).then((d) => {
          if (d.ok) setDetails((prev) => ({ ...prev, [c.id]: d.data }));
        });
      });
    });
  }
  useEffect(load, []);

  const campaigns = state.status === "loaded" ? state.campaigns : [];

  const groups = useMemo(() => {
    const g: Record<Bucket, CampaignDetail[]> = { launched: [], ready: [], blocked: [] };
    campaigns.forEach((c) => g[bucketOf(c)].push(c));
    return g;
  }, [campaigns]);

  // Real aggregate stats from the loaded details (skeleton until they arrive).
  const detailList = Object.values(details);
  const stats = useMemo(() => {
    const replies = detailList.reduce(
      (n, d) => n + (d.workflows || []).filter((w) => w.reply_detected).length,
      0,
    );
    const scores = detailList.flatMap((d) =>
      (d.result?.prospects || [])
        .map((p) => p.qualification?.qualification_score)
        .filter((s): s is number => typeof s === "number"),
    );
    const avgFit = scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : null;
    return {
      active: groups.launched.length,
      replies,
      avgFit,
      ready: detailList.length < campaigns.length, // still loading details
    };
  }, [detailList, groups.launched.length, campaigns.length]);

  return (
    <div>
      <PageHeader
        title="Campaigns"
        subtitle="Every sequence Saqua has previewed or launched, grouped by where it stands."
        actions={
          <Button asChild variant="primary">
            <Link href="/campaigns/new">
              <Plus className="size-4" /> New campaign
            </Link>
          </Button>
        }
      />

      {/* Stats strip */}
      <div className="mb-6 grid gap-3 sm:grid-cols-3">
        <Stat label="Active campaigns" value={String(stats.active)} icon={Rocket} />
        <Stat
          label="Replies"
          value={stats.ready ? null : String(stats.replies)}
          icon={MailCheck}
          accent={stats.replies > 0}
        />
        <Stat label="Avg fit score" value={stats.ready ? null : stats.avgFit === null ? "-" : `${stats.avgFit}`} icon={Megaphone} />
      </div>

      {state.status === "loading" && (
        <Card>
          <div className="space-y-2 p-4">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-14" />
            ))}
          </div>
        </Card>
      )}

      {state.status === "error" && (
        <EmptyState
          icon={Megaphone}
          title="Couldn't load campaigns"
          body={`Saqua could not reach the campaign backend: ${state.error}`}
          action={
            <Button variant="primary" onClick={load}>
              <RefreshCw className="size-4" /> Retry
            </Button>
          }
        />
      )}

      {state.status === "loaded" && campaigns.length === 0 && (
        <EmptyState
          icon={Megaphone}
          title="No campaigns yet"
          body="Create your first campaign and Saqua will take it from ICP to a safe, guard-approved launch."
          action={
            <Button asChild variant="primary">
              <Link href="/campaigns/new">
                <Plus className="size-4" /> New campaign
              </Link>
            </Button>
          }
        />
      )}

      {state.status === "loaded" && campaigns.length > 0 && (
        <div className="space-y-8">
          {(["launched", "ready", "blocked"] as Bucket[]).map((b) =>
            groups[b].length === 0 ? null : (
              <section key={b}>
                <div className="mb-3 flex items-center gap-2.5">
                  <span className={cn("size-2 rounded-full", BUCKET_META[b].dot)} />
                  <h2 className="text-sm font-semibold text-text">{BUCKET_META[b].label}</h2>
                  <span className="font-mono text-xs text-muted">{groups[b].length}</span>
                  <span className="text-xs text-muted">· {BUCKET_META[b].hint}</span>
                </div>
                <div className="space-y-2">
                  {groups[b].map((c) => (
                    <CampaignRow key={c.id} campaign={c} detail={details[c.id]} />
                  ))}
                </div>
              </section>
            ),
          )}
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  icon: Icon,
  accent,
}: {
  label: string;
  value: string | null;
  icon: typeof Rocket;
  accent?: boolean;
}) {
  return (
    <Card className="flex items-center gap-3 px-4 py-3.5">
      <span className={cn("grid size-9 shrink-0 place-items-center rounded-md", accent ? "bg-accent-soft text-accent" : "bg-black/[0.04] text-text-2")}>
        <Icon className="size-4" />
      </span>
      <div className="min-w-0">
        <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
        {value === null ? (
          <Skeleton className="mt-1 h-5 w-10" />
        ) : (
          <div className={cn("font-mono text-lg font-semibold", accent ? "text-accent" : "text-text")}>{value}</div>
        )}
      </div>
    </Card>
  );
}

function CampaignRow({ campaign: c, detail }: { campaign: CampaignDetail; detail?: CampaignDetail }) {
  const [open, setOpen] = useState(false);
  const s = c.summary;
  const workflows = detail?.workflows || [];
  const prospects = detail?.result?.prospects || [];
  const replies = workflows.filter((w) => w.reply_detected).length;
  const launchable = s?.launchable ?? 0;
  const discovered = s?.discovered ?? 0;
  const noneReachable = discovered > 0 && launchable === 0;

  return (
    <Card className="overflow-hidden">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-3 px-5 py-3.5 text-left">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-text">{c.name}</span>
            <StatusPill state={c.status} />
            {replies > 0 && (
              <span className="inline-flex items-center gap-1 rounded-full border border-accent-line bg-accent-soft px-2 py-0.5 text-[11px] text-accent">
                <MailCheck className="size-3" /> {replies} {replies === 1 ? "reply" : "replies"}
              </span>
            )}
          </div>
          <div className="mt-0.5 text-xs text-muted">{timeAgo(c.created_at)}</div>
        </div>
        <div className="hidden items-center gap-5 sm:flex">
          <Metric label="Discovered" value={discovered} />
          <Metric label="Launchable" value={launchable} accent={launchable > 0} />
          <Metric label="Est. cost" value={fmtCurrency(s?.estimated_cost, 2)} />
        </div>
        <ChevronDown className={cn("size-4 shrink-0 text-muted transition-transform", open && "rotate-180")} />
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 0.61, 0.36, 1] }}
            className="overflow-hidden border-t border-border-faint"
          >
            <div className="space-y-4 px-5 py-4">
              {/* Zero-reachable failure is surfaced, not hidden. */}
              {noneReachable && (
                <div className="flex items-start gap-2.5 rounded-md border border-danger-soft bg-danger-soft/40 px-3 py-2.5 text-xs text-text-2">
                  <Ban className="mt-0.5 size-4 shrink-0 text-danger" />
                  <span>
                    <span className="font-medium text-danger">Nothing launched.</span> All{" "}
                    {discovered} discovered {discovered === 1 ? "prospect" : "prospects"} were blocked, held, or
                    had no valid recipient, so this campaign has no live sequence.{" "}
                    {detail && <ReasonBreakdown prospects={prospects} />}
                  </span>
                </div>
              )}

              {/* Live sequences (post-launch state per prospect) */}
              {workflows.length > 0 && (
                <div>
                  <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted">
                    Live sequences ({workflows.length})
                  </div>
                  <div className="space-y-1.5">
                    {workflows.map((w) => (
                      <WorkflowRow key={w.id} w={w} />
                    ))}
                  </div>
                </div>
              )}

              {/* Prospects — full pipeline + why any are blocked (shared card) */}
              {!detail ? (
                <div className="space-y-2">
                  {[0, 1].map((i) => (
                    <Skeleton key={i} className="h-16" />
                  ))}
                </div>
              ) : prospects.length > 0 ? (
                <div>
                  <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted">
                    Prospects ({prospects.length})
                  </div>
                  <div className="space-y-2">
                    {prospects.map((p, i) => (
                      <ProspectCard key={p.domain || i} prospect={p} index={i} />
                    ))}
                  </div>
                </div>
              ) : (
                <p className="text-xs text-muted">No prospects were produced for this campaign.</p>
              )}

              <Link
                href={`/campaigns/${c.id}`}
                className="inline-flex items-center gap-1.5 text-xs text-accent hover:underline"
              >
                Open full campaign <ArrowRight className="size-3.5" />
              </Link>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  );
}

function ReasonBreakdown({ prospects }: { prospects: CampaignDetail["result"]["prospects"] }) {
  const counts: Record<string, number> = {};
  prospects.forEach((p) => {
    const k = p.final_status || "unknown";
    counts[k] = (counts[k] || 0) + 1;
  });
  const LABEL: Record<string, string> = {
    guard_blocked: "guard-blocked",
    qualification_blocked: "not qualified",
    route_only: "no valid recipient",
    writer_failed: "draft failed",
    research_failed: "research failed",
    timed_out: "ran out of time",
  };
  const parts = Object.entries(counts)
    .filter(([k]) => k !== "sendable")
    .map(([k, n]) => `${n} ${LABEL[k] || k}`);
  return parts.length ? <span className="text-muted">({parts.join(", ")})</span> : null;
}

function WorkflowRow({ w }: { w: WorkflowStatus }) {
  const waiting = !w.reply_detected && w.next_run_at ? new Date(w.next_run_at * 1000) : null;
  return (
    <div className="flex items-center gap-3 rounded-md border border-border-faint bg-black/[0.02] px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs text-text">{w.company || w.to || "Workflow"}</div>
        <div className="truncate font-mono text-[11px] text-muted">{w.to}</div>
      </div>
      {w.reply_detected ? (
        <StatusPill state="replied" />
      ) : (
        <StatusPill state={w.state} />
      )}
      <span className="hidden items-center gap-1 font-mono text-[11px] text-muted sm:inline-flex">
        {w.current_step}/{w.total_steps}
      </span>
      {waiting && (
        <span className="hidden items-center gap-1 text-[11px] text-muted md:inline-flex">
          <Clock className="size-3" />
          {waiting.toLocaleDateString(undefined, { month: "short", day: "numeric" })}
        </span>
      )}
    </div>
  );
}

function Metric({ label, value, accent }: { label: string; value: string | number; accent?: boolean }) {
  return (
    <div className="text-right">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className={cn("font-mono text-sm", accent ? "font-semibold text-accent" : "text-text-2")}>{value}</div>
    </div>
  );
}
