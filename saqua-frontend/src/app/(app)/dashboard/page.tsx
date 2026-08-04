"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, MailCheck, Megaphone, Plus, Reply, Send } from "lucide-react";
import { Badge, StatusPill } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { StatCard } from "@/components/dashboard/stat-card";
import { api, type AutomationMetrics, type WorkflowStatus } from "@/lib/api";

type DashboardState =
  | { status: "loading"; workflows: WorkflowStatus[]; metrics: AutomationMetrics | null }
  | { status: "error"; workflows: WorkflowStatus[]; metrics: AutomationMetrics | null; error: string }
  | { status: "loaded"; workflows: WorkflowStatus[]; metrics: AutomationMetrics };

export default function DashboardPage() {
  const [state, setState] = useState<DashboardState>({ status: "loading", workflows: [], metrics: null });

  function loadDashboard() {
    setState((current) => ({ status: "loading", workflows: current.workflows, metrics: current.metrics }));
    void Promise.all([api.workflows(), api.metrics()]).then(([workflowsResult, metricsResult]) => {
      if (!workflowsResult.ok || !metricsResult.ok) {
        const error = !workflowsResult.ok ? workflowsResult.error : !metricsResult.ok ? metricsResult.error : "Unknown error";
        setState({
          status: "error",
          workflows: workflowsResult.ok ? workflowsResult.data.workflows : [],
          metrics: metricsResult.ok ? metricsResult.data : null,
          error,
        });
        return;
      }
      setState({ status: "loaded", workflows: workflowsResult.data.workflows, metrics: metricsResult.data });
    });
  }

  useEffect(() => {
    loadDashboard();
  }, []);

  const workflows = state.workflows;
  const metrics = state.metrics?.metrics;
  const hasCampaigns = workflows.length > 0;
  const activeCampaigns = useMemo(
    () => workflows.filter((workflow) => !["COMPLETED", "CANCELLED", "STOPPED", "FAILED"].includes(workflow.state.toUpperCase())),
    [workflows],
  );
  const emailsSent = metrics?.emails_sent ?? 0;
  const replies = metrics?.replies ?? 0;
  const replyRate = metrics?.reply_rate ? `${(metrics.reply_rate * 100).toFixed(1)}%` : "0.0%";

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="The shortest path from idea to a safe outbound campaign."
        actions={
          <Button asChild variant="primary">
            <Link href="/campaigns/new">
              <Plus className="size-4" /> Create campaign
            </Link>
          </Button>
        }
      />

      {state.status === "loading" && !state.metrics ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {[0, 1, 2, 3].map((item) => <Skeleton key={item} className="h-32" />)}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard label="Emails sent" value={emailsSent.toLocaleString()} delta={0} icon={Send} index={0} />
          <StatCard label="Replies" value={replies.toLocaleString()} delta={0} icon={Reply} index={1} />
          <StatCard label="Reply rate" value={replyRate} delta={0} icon={MailCheck} index={2} />
          <StatCard label="Campaigns active" value={activeCampaigns.length.toString()} delta={0} icon={Megaphone} index={3} />
        </div>
      )}

      {state.status === "error" && (
        <div className="mt-4">
          <EmptyState
            icon={Megaphone}
            title="Couldn't load your dashboard"
            body={`Saqua couldn't fetch your sending activity just now, so these numbers aren't showing. Nothing about your campaigns has changed, and anything scheduled is still running. Try again in a moment. (${state.error})`}
            action={<Button variant="primary" onClick={loadDashboard}>Retry dashboard</Button>}
          />
        </div>
      )}

      <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_380px]">
        <Card>
          <CardHeader>
            <CardTitle>Recent campaigns</CardTitle>
            <Button asChild variant="ghost" size="sm" className="text-accent hover:bg-transparent">
              <Link href="/campaigns/saas-founders-us">
                Open <ArrowRight className="size-3.5" />
              </Link>
            </Button>
          </CardHeader>
          {state.status === "loading" ? (
            <div className="space-y-3 p-5">
              <Skeleton className="h-16" />
              <Skeleton className="h-16" />
              <Skeleton className="h-16" />
            </div>
          ) : hasCampaigns ? (
            <div className="divide-y divide-border-faint">
              {workflows.slice(0, 6).map((campaign) => (
                <Link
                  key={campaign.id}
                  href="/campaigns/saas-founders-us"
                  className="grid gap-3 px-5 py-4 transition-all duration-200 hover:bg-black/[0.02] md:grid-cols-[1fr_110px_90px_90px_90px]"
                >
                  <div>
                    <div className="text-sm font-medium text-text">{campaign.company || campaign.to || "Untitled campaign"}</div>
                    <div className="mt-0.5 text-xs text-muted">{campaign.provider} workflow</div>
                  </div>
                  <StatusPill state={campaign.state} />
                  <div className="text-sm text-text-2">{campaign.current_step}/{campaign.total_steps} steps</div>
                  <div className="text-sm text-text-2">{campaign.reply_detected ? "reply" : "no reply"}</div>
                  <div className="text-sm font-medium text-accent">{campaign.retry_count} retries</div>
                </Link>
              ))}
            </div>
          ) : (
            <div className="p-5">
              <EmptyState
                icon={Megaphone}
                title="No campaigns yet."
                body="Create your first campaign and Saqua will guide you from ICP to launch."
                action={
                  <Button asChild variant="primary">
                    <Link href="/campaigns/new">
                      <Plus className="size-4" /> Create campaign
                    </Link>
                  </Button>
                }
              />
            </div>
          )}
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Active campaigns</CardTitle>
            <Badge tone="accent" dot>live</Badge>
          </CardHeader>
          <CardContent className="space-y-3">
            {state.status === "loading" && (
              <>
                <Skeleton className="h-20" />
                <Skeleton className="h-20" />
              </>
            )}
            {state.status !== "loading" && activeCampaigns.length === 0 && (
              <EmptyState
                icon={Megaphone}
                title="No campaigns running yet"
                body="Describe who you want to reach and Saqua will find the companies, research them, and draft the emails. You review everything before anything sends."
              />
            )}
            {state.status !== "loading" && activeCampaigns.map((workflow) => (
              <Link
                key={workflow.id}
                href="/campaigns/saas-founders-us"
                className="block rounded-md border border-border-faint bg-black/[0.02] p-3 transition-colors hover:border-border-strong"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm font-medium text-text">{workflow.company || workflow.to || "Untitled campaign"}</div>
                  <StatusPill state={workflow.state} />
                </div>
                <div className="mt-2 flex items-center justify-between text-xs text-muted">
                  <span>Step {workflow.current_step} of {workflow.total_steps}</span>
                  <span>{workflow.provider}</span>
                </div>
              </Link>
            ))}
          </CardContent>
        </Card>
      </div>

      {hasCampaigns && (
        <div className="relative mt-4 overflow-hidden rounded-xl border border-white/10 bg-[#13141A]">
          {/* A very subtle blue-purple accent anchored near the button, not a large gradient. */}
          <div
            aria-hidden
            className="pointer-events-none absolute right-0 top-1/2 size-64 -translate-y-1/2 translate-x-1/3 rounded-full bg-[radial-gradient(circle,rgba(124,92,255,0.16),transparent_70%)]"
          />
          <div className="relative flex flex-col gap-4 px-7 py-6 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="text-base font-semibold text-white">Ready to launch the next campaign?</div>
              <p className="mt-1 text-sm text-white/60">Define your ICP, let Saqua find the leads, then review before sending.</p>
            </div>
            <Button asChild variant="primary" className="shrink-0">
              <Link href="/campaigns/new">
                <Plus className="size-4" /> Create campaign
              </Link>
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
