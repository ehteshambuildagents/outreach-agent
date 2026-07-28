"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  Clock,
  Mail,
  Pause,
  Play,
  Reply,
  RotateCcw,
  Send,
  ShieldCheck,
  StopCircle,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { motion } from "framer-motion";
import Link from "next/link";
import { StatusPill } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type WorkflowEvent, type WorkflowStatus } from "@/lib/api";

type CampaignState =
  | { status: "loading"; workflow: null; events: WorkflowEvent[]; error: "" }
  | { status: "error"; workflow: null; events: WorkflowEvent[]; error: string }
  | { status: "empty"; workflow: null; events: WorkflowEvent[]; error: "" }
  | { status: "loaded"; workflow: WorkflowStatus; events: WorkflowEvent[]; error: "" };

export default function CampaignDetailPage() {
  const [state, setState] = useState<CampaignState>({ status: "loading", workflow: null, events: [], error: "" });
  const [actionError, setActionError] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);

  function loadCampaign() {
    setState((current) => ({ status: "loading", workflow: current.workflow, events: current.events, error: "" }) as CampaignState);
    setActionError("");
    void api.workflows().then(async (workflowsResult) => {
      if (!workflowsResult.ok) {
        setState({ status: "error", workflow: null, events: [], error: workflowsResult.error });
        return;
      }
      const first = workflowsResult.data.workflows[0];
      if (!first) {
        setState({ status: "empty", workflow: null, events: [], error: "" });
        return;
      }
      const detail = await api.workflow(first.id);
      if (!detail.ok) {
        setState({ status: "error", workflow: null, events: [], error: detail.error });
        return;
      }
      setState({ status: "loaded", workflow: detail.data, events: detail.data.events || [], error: "" });
    });
  }

  useEffect(() => {
    loadCampaign();
  }, []);

  const timeline = useMemo(() => buildTimeline(state.events, state.workflow), [state.events, state.workflow]);

  function runAction(label: string, action: (id: string) => Promise<{ ok: true; data: WorkflowStatus } | { ok: false; error: string; status?: number }>) {
    if (!state.workflow) return;
    setBusyAction(label);
    setActionError("");
    void action(state.workflow.id).then((result) => {
      setBusyAction(null);
      if (!result.ok) {
        setActionError(result.error);
        return;
      }
      loadCampaign();
    });
  }

  if (state.status === "empty") {
    return (
      <EmptyState
        icon={Workflow}
        title="Nothing is sending yet"
        body="Once you launch a campaign, its live sequence status, timeline and replies show up here."
        action={
          <Button asChild variant="primary">
            <Link href="/campaigns/new">Create campaign</Link>
          </Button>
        }
      />
    );
  }

  if (state.status === "error") {
    return (
      <EmptyState
        icon={Workflow}
        title="Couldn't load sequence status"
        body={`Saqua couldn't fetch this sequence's live status just now. Sending is unaffected: anything scheduled is still running, and nothing has been paused. Try again in a moment. (${state.error})`}
        action={<Button variant="primary" onClick={loadCampaign}>Try again</Button>}
      />
    );
  }

  const workflow = state.workflow;
  const title = workflow?.company || workflow?.to || "Campaigns";
  const isLoaded = state.status === "loaded" && Boolean(workflow);

  return (
    <div>
      <PageHeader
        title={isLoaded ? title : "Campaigns"}
        subtitle={isLoaded ? `${workflow?.provider || "Automation"} workflow status, timeline, replies, and controls.` : "Loading campaign status from automation."}
        actions={
          <>
            <Button
              variant="secondary"
              onClick={() => runAction("pause", api.pause)}
              disabled={!isLoaded || busyAction !== null}
            >
              <Pause className="size-4" /> Pause
            </Button>
            <Button
              variant="secondary"
              onClick={() => runAction("resume", api.resume)}
              disabled={!isLoaded || busyAction !== null}
            >
              <Play className="size-4" /> Resume
            </Button>
            <Button
              variant="danger"
              onClick={() => runAction("cancel", api.cancel)}
              disabled={!isLoaded || busyAction !== null}
            >
              <StopCircle className="size-4" /> Cancel
            </Button>
          </>
        }
      />

      {actionError && (
        <div className="mb-4 rounded-md border border-danger-soft bg-danger-soft p-3 text-sm text-danger">
          {actionError}
        </div>
      )}

      {state.status === "loading" && !workflow ? (
        <>
          <div className="mb-4 grid gap-4 md:grid-cols-4">
            {[0, 1, 2, 3].map((item) => <Skeleton key={item} className="h-24" />)}
          </div>
          <Skeleton className="h-[520px]" />
        </>
      ) : workflow ? (
        <>
          <div className="mb-4 grid gap-4 md:grid-cols-4">
            <Metric label="Status" value={workflow.state} pill />
            <Metric label="Step" value={`${workflow.current_step}/${workflow.total_steps}`} />
            <Metric label="Provider" value={workflow.provider} />
            <Metric label="Retries" value={workflow.retry_count.toString()} accent={workflow.retry_count > 0} />
          </div>

          <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>Campaign timeline</CardTitle>
                  <Workflow className="size-4 text-muted" />
                </CardHeader>
                <CardContent>
                  <div className="relative space-y-4">
                    <div className="absolute left-[19px] top-5 h-[calc(100%-40px)] w-px bg-border" />
                    {timeline.map((event, index) => {
                      const Icon = event.icon;
                      return (
                        <motion.div
                          key={`${event.title}-${event.timestamp}-${index}`}
                          initial={{ opacity: 0, y: 8 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ delay: index * 0.035 }}
                          className="relative flex gap-4"
                        >
                          <div className={`z-10 grid size-10 place-items-center rounded-full border ${event.status === "completed" ? "border-success-soft bg-success-soft text-success shadow-glow" : event.status === "active" ? "border-accent-line bg-accent-soft text-accent shadow-glow" : "border-border bg-card text-muted"}`}>
                            <Icon className="size-4" />
                          </div>
                          <div className="flex-1 rounded-lg border border-border-faint bg-black/[0.02] p-4 transition-colors hover:border-border-strong">
                            <div className="flex flex-wrap items-center justify-between gap-3">
                              <div>
                                <div className="text-sm font-medium text-text">{event.title}</div>
                                <div className="mt-1 text-xs text-muted">{event.detail}</div>
                              </div>
                              <div className="flex items-center gap-3">
                                <span className="text-xs text-muted">{event.timestamp}</span>
                                <StatusPill state={event.status} />
                              </div>
                            </div>
                          </div>
                        </motion.div>
                      );
                    })}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Email workflow</CardTitle>
                  <Mail className="size-4 text-muted" />
                </CardHeader>
                <div className="divide-y divide-border-faint">
                  <div className="grid gap-3 px-5 py-4 md:grid-cols-[180px_1fr_100px]">
                    <div className="text-sm font-medium text-text">{workflow.to}</div>
                    <div className="truncate text-sm text-muted">{workflow.company || "Automation sequence"}</div>
                    <StatusPill state={workflow.reply_detected ? "reply" : workflow.state} />
                  </div>
                </div>
              </Card>
            </div>

            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>Replies</CardTitle>
                  <Reply className="size-4 text-muted" />
                </CardHeader>
                <CardContent className="space-y-3">
                  {workflow.reply_detected ? (
                    <div className="rounded-md border border-success-soft bg-success-soft p-3">
                      <div className="text-sm font-medium text-success">Reply detected</div>
                      <p className="mt-1 text-xs leading-5 text-text-2">Saqua stopped this sequence automatically, so no follow-ups will go out. Reply to them yourself from your inbox.</p>
                    </div>
                  ) : (
                    <div className="rounded-md border border-border-faint bg-black/[0.02] p-3 text-xs text-muted">
                      No replies detected yet.
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Automation controls</CardTitle>
                  <RotateCcw className="size-4 text-muted" />
                </CardHeader>
                <CardContent className="space-y-3">
                  <Button variant="secondary" className="w-full justify-start" onClick={() => runAction("run", api.run)} disabled={busyAction !== null}>
                    <Send className="size-4" /> Run next step
                  </Button>
                  <Button variant="secondary" className="w-full justify-start" onClick={() => runAction("force-retry", api.forceRetry)} disabled={busyAction !== null}>
                    <RotateCcw className="size-4" /> Force retry
                  </Button>
                  <Button variant="secondary" className="w-full justify-start" onClick={() => runAction("force-complete", api.forceComplete)} disabled={busyAction !== null}>
                    <CheckCircle2 className="size-4" /> Force complete
                  </Button>
                </CardContent>
              </Card>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

function buildTimeline(events: WorkflowEvent[], workflow: WorkflowStatus | null) {
  if (events.length > 0) {
    return events.map((event) => ({
      title: titleCase(event.type || event.event || event.state || "event"),
      detail: event.detail || event.message || "Automation event recorded",
      timestamp: formatTime(event.ts ?? event.at),
      status: eventStatus(event.type || event.state),
      icon: eventIcon(event.type || event.state),
    }));
  }

  if (!workflow) return [];

  return [
    { title: "Campaign created", detail: `${workflow.total_steps} email step${workflow.total_steps === 1 ? "" : "s"} via ${workflow.provider}`, timestamp: "Loaded", status: "completed", icon: ShieldCheck },
    { title: "Current state", detail: `Step ${workflow.current_step} of ${workflow.total_steps}`, timestamp: workflow.next_run_at ? formatTime(workflow.next_run_at) : "Now", status: "active", icon: Clock },
    { title: "Reply detection", detail: workflow.reply_detected ? "Reply detected; workflow stopped" : "Watching for replies", timestamp: workflow.reply_detected ? "Done" : "Pending", status: workflow.reply_detected ? "completed" : "queued", icon: Reply },
  ];
}

function eventStatus(value?: string) {
  const normalized = (value || "").toLowerCase();
  if (["created", "sent", "reply", "stopped", "completed", "resumed", "paused", "cancelled"].includes(normalized)) return "completed";
  if (["queued", "running", "sending"].includes(normalized)) return "active";
  return "queued";
}

function eventIcon(value?: string): LucideIcon {
  const normalized = (value || "").toLowerCase();
  if (normalized.includes("sent")) return Send;
  if (normalized.includes("reply") || normalized.includes("stopped")) return Reply;
  if (normalized.includes("cancel")) return StopCircle;
  if (normalized.includes("pause")) return Pause;
  if (normalized.includes("complete")) return CheckCircle2;
  return ShieldCheck;
}

function titleCase(value: string) {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatTime(value?: number) {
  if (!value) return "Pending";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value * 1000));
}

function Metric({ label, value, accent, pill }: { label: string; value: string; accent?: boolean; pill?: boolean }) {
  return (
    <Card className="p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className={`mt-2 text-2xl font-semibold ${accent ? "text-accent" : "text-text"}`}>
        {pill ? <StatusPill state={value} /> : value}
      </div>
    </Card>
  );
}
