"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  Rocket,
  Pause,
  Play,
  Ban,
  Loader2,
  RefreshCw,
  AlertTriangle,
  Workflow as WorkflowIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusPill } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { SummaryGrid } from "@/components/campaign/summary-grid";
import { ProspectCard } from "@/components/campaign/prospect-card";
import { api, type CampaignDetail } from "@/lib/api";
import { cn } from "@/lib/utils";

type Provider = "dryrun" | "gmail" | "outlook";
type State =
  | { status: "loading" }
  | { status: "error"; error: string; notFound?: boolean }
  | { status: "loaded"; campaign: CampaignDetail };

export default function CampaignDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [state, setState] = useState<State>({ status: "loading" });
  const [provider, setProvider] = useState<Provider>("dryrun");
  const [busy, setBusy] = useState<string>("");
  const [actionError, setActionError] = useState("");

  function load() {
    setState({ status: "loading" });
    void api.campaign(id).then((res) => {
      if (!res.ok) setState({ status: "error", error: res.error, notFound: res.status === 404 });
      else setState({ status: "loaded", campaign: res.data });
    });
  }
  useEffect(() => {
    if (id) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function act(kind: "launch" | "pause" | "resume" | "cancel") {
    setActionError("");
    setBusy(kind);
    const res =
      kind === "launch"
        ? await api.launchCampaign(id, provider)
        : kind === "pause"
          ? await api.pauseCampaign(id)
          : kind === "resume"
            ? await api.resumeCampaign(id)
            : await api.cancelCampaign(id);
    setBusy("");
    if (!res.ok) {
      setActionError(
        res.status === 400 && kind === "launch"
          ? "No guard-approved emails with a valid recipient address. Nothing was launched."
          : `${kind} failed: ${res.error}`,
      );
      return;
    }
    setState({ status: "loaded", campaign: res.data });
  }

  if (state.status === "loading") {
    return (
      <div>
        <BackLink />
        <Skeleton className="mb-4 h-9 w-64" />
        <Skeleton className="h-24" />
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div>
        <BackLink />
        <EmptyState
          icon={AlertTriangle}
          title={state.notFound ? "Campaign not found" : "Couldn't load campaign"}
          body={state.notFound ? "This campaign does not exist or belongs to another account." : state.error}
          action={
            state.notFound ? (
              <Button asChild variant="secondary">
                <Link href="/campaigns">Back to campaigns</Link>
              </Button>
            ) : (
              <Button variant="primary" onClick={load}>
                <RefreshCw className="size-4" /> Retry
              </Button>
            )
          }
        />
      </div>
    );
  }

  const c = state.campaign;
  const summary = c.result?.summary ?? c.summary;
  const prospects = c.result?.prospects ?? [];
  const launched = c.status === "launched" || c.workflow_ids.length > 0;
  const launchable = summary?.launchable ?? 0;

  return (
    <div>
      <BackLink />
      <PageHeader
        title={c.name}
        subtitle={<span className="capitalize">{c.status} · {summary?.discovered ?? 0} prospects discovered</span>}
        actions={
          <div className="flex items-center gap-2">
            <StatusPill state={c.status} />
            {!launched ? (
              <>
                <ProviderSelect value={provider} onChange={setProvider} />
                <Button variant="primary" disabled={launchable === 0 || busy !== ""} onClick={() => act("launch")}>
                  {busy === "launch" ? <Loader2 className="size-4 animate-spin" /> : <Rocket className="size-4" />}
                  Launch
                </Button>
              </>
            ) : (
              <>
                {c.status === "paused" ? (
                  <Button variant="secondary" disabled={busy !== ""} onClick={() => act("resume")}>
                    <Play className="size-4" /> Resume
                  </Button>
                ) : (
                  <Button variant="secondary" disabled={busy !== ""} onClick={() => act("pause")}>
                    <Pause className="size-4" /> Pause
                  </Button>
                )}
                <Button variant="danger" disabled={busy !== ""} onClick={() => act("cancel")}>
                  <Ban className="size-4" /> Cancel
                </Button>
              </>
            )}
          </div>
        }
      />

      {actionError && (
        <div className="mb-4 rounded-md border border-[color:var(--danger-soft)] bg-danger-soft px-4 py-2.5 text-xs text-danger">
          {actionError}
        </div>
      )}

      <div className="space-y-4">
        <SummaryGrid summary={summary} />

        {launched && c.workflows.length > 0 && (
          <Card className="overflow-hidden">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <WorkflowIcon className="size-4 text-accent" /> Live workflows
              </CardTitle>
              <span className="text-xs text-muted">{c.workflows.length} running</span>
            </CardHeader>
            <div className="divide-y divide-border-faint">
              {c.workflows.map((w) => (
                <div key={w.id} className="grid grid-cols-[1fr_110px_90px_90px] items-center gap-3 px-5 py-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm text-text">{w.company || w.to || "Workflow"}</div>
                    <div className="text-xs text-muted">{w.to}</div>
                  </div>
                  <StatusPill state={w.state} />
                  <span className="text-sm text-text-2">
                    {w.current_step}/{w.total_steps} steps
                  </span>
                  <span className="text-sm text-text-2">{w.reply_detected ? "reply" : "-"}</span>
                </div>
              ))}
            </div>
          </Card>
        )}

        <div>
          <div className="mb-2 px-1 text-xs font-medium uppercase tracking-wide text-muted">
            Prospects ({prospects.length})
          </div>
          <div className="grid gap-3">
            {prospects.length === 0 ? (
              <Card>
                <CardContent className="py-6 text-center text-sm text-muted">
                  No prospects were produced for this campaign.
                </CardContent>
              </Card>
            ) : (
              prospects.map((p, i) => <ProspectCard key={p.domain || i} prospect={p} index={i} />)
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      href="/campaigns"
      className="mb-4 inline-flex items-center gap-1.5 text-xs text-muted transition-colors hover:text-text-2"
    >
      <ArrowLeft className="size-3.5" /> Campaigns
    </Link>
  );
}

function ProviderSelect({ value, onChange }: { value: Provider; onChange: (p: Provider) => void }) {
  const opts: Provider[] = ["dryrun", "gmail", "outlook"];
  return (
    <div className="flex items-center gap-0.5 rounded-sm border border-border bg-black/[0.02] p-0.5">
      {opts.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          className={cn(
            "rounded-[6px] px-2.5 py-1.5 text-xs font-medium capitalize transition-colors",
            value === o ? "bg-accent-soft text-accent" : "text-muted hover:text-text-2",
          )}
        >
          {o}
        </button>
      ))}
    </div>
  );
}
