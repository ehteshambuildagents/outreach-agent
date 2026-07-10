"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Megaphone, Plus, RefreshCw, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusPill } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type CampaignDetail } from "@/lib/api";
import { fmtCurrency, timeAgo } from "@/lib/utils";

type State =
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "loaded"; campaigns: CampaignDetail[] };

export default function CampaignsPage() {
  const [state, setState] = useState<State>({ status: "loading" });

  function load() {
    setState({ status: "loading" });
    void api.campaigns().then((res) => {
      if (!res.ok) setState({ status: "error", error: res.error });
      else setState({ status: "loaded", campaigns: res.data.campaigns });
    });
  }
  useEffect(load, []);

  return (
    <div>
      <PageHeader
        title="Campaigns"
        subtitle="Every campaign Saqua has previewed or launched."
        actions={
          <Button asChild variant="primary">
            <Link href="/campaigns/new">
              <Plus className="size-4" /> New campaign
            </Link>
          </Button>
        }
      />

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

      {state.status === "loaded" && state.campaigns.length === 0 && (
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

      {state.status === "loaded" && state.campaigns.length > 0 && (
        <Card className="overflow-hidden">
          <CardHeader>
            <CardTitle>All campaigns</CardTitle>
            <span className="text-xs text-muted">{state.campaigns.length} total</span>
          </CardHeader>
          <div className="grid grid-cols-[1fr_110px_90px_90px_100px_36px] gap-3 border-y border-border-faint px-5 py-2.5 text-[11px] font-medium uppercase tracking-wide text-muted">
            <span>Campaign</span>
            <span>Status</span>
            <span>Discovered</span>
            <span>Launchable</span>
            <span>Est. cost</span>
            <span />
          </div>
          <div className="divide-y divide-border-faint">
            {state.campaigns.map((c) => {
              const s = c.summary || c.result?.summary;
              return (
                <Link
                  key={c.id}
                  href={`/campaigns/${c.id}`}
                  className="grid grid-cols-[1fr_110px_90px_90px_100px_36px] items-center gap-3 px-5 py-3.5 transition-colors hover:bg-white/[0.02]"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-text">{c.name}</div>
                    <div className="mt-0.5 text-xs text-muted">{timeAgo(c.created_at)}</div>
                  </div>
                  <StatusPill state={c.status} />
                  <span className="text-sm text-text-2">{s?.discovered ?? 0}</span>
                  <span className="text-sm font-medium text-accent-hi">{s?.launchable ?? 0}</span>
                  <span className="text-sm text-text-2">{fmtCurrency(s?.estimated_cost, 2)}</span>
                  <ArrowRight className="size-4 text-muted" />
                </Link>
              );
            })}
          </div>
        </Card>
      )}
    </div>
  );
}
