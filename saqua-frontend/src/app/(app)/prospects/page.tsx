"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { Eye, Plus, Users, X, Globe, Sparkles, RefreshCw, MessageSquare } from "lucide-react";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { DataTable, Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ResearchTrail } from "@/components/chat/research-trail";
import { api, type DiscoveryProspect } from "@/lib/api";

type State =
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "loaded"; prospects: DiscoveryProspect[] };

function pct(c?: number | null): string | null {
  return typeof c === "number" ? `${Math.round(c * 100)}%` : null;
}

function host(url?: string | null): string {
  if (!url) return "";
  try {
    return new URL(url.startsWith("http") ? url : `https://${url}`).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export default function ProspectsPage() {
  const [state, setState] = useState<State>({ status: "loading" });
  const [selected, setSelected] = useState<DiscoveryProspect | null>(null);

  function load() {
    setState({ status: "loading" });
    void api.prospects().then((res) => {
      if (!res.ok) setState({ status: "error", error: res.error });
      else setState({ status: "loaded", prospects: res.data.prospects || [] });
    });
  }
  useEffect(load, []);

  return (
    <div>
      <PageHeader
        title="Prospects"
        subtitle="Companies Saqua discovered matching your ICP. They accumulate here as you research."
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
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        </Card>
      )}

      {state.status === "error" && (
        <EmptyState
          icon={Users}
          title="Couldn't load your prospects"
          body={`Saqua couldn't fetch your saved prospects just now. None of them have been lost, this only affects the list on this page. Try again in a moment. (${state.error})`}
          action={
            <Button variant="primary" onClick={load}>
              <RefreshCw className="size-4" /> Retry
            </Button>
          }
        />
      )}

      {/* Genuinely empty until the user actually discovers/researches companies —
          no demo names. */}
      {state.status === "loaded" && state.prospects.length === 0 && (
        <EmptyState
          icon={Users}
          title="No prospects yet"
          body="Ask Saqua in Chat to find companies matching your ICP, or launch a campaign. Everyone it discovers and qualifies will show up here."
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <Button asChild variant="primary">
                <Link href="/ai">
                  <MessageSquare className="size-4" /> Find prospects in Chat
                </Link>
              </Button>
              <Button asChild variant="secondary">
                <Link href="/campaigns/new">
                  <Plus className="size-4" /> New campaign
                </Link>
              </Button>
            </div>
          }
        />
      )}

      {state.status === "loaded" && state.prospects.length > 0 && (
        <DataTable>
          <Table>
            <THead>
              <TR>
                <TH>Company</TH>
                <TH>Industry · Stage</TH>
                <TH>Location</TH>
                <TH className="text-right">Match</TH>
                <TH>Why it matches</TH>
                <TH className="text-right">Action</TH>
              </TR>
            </THead>
            <TBody>
              {state.prospects.map((p, i) => (
                <TR key={`${p.company_name}-${i}`}>
                  <TD>
                    <button onClick={() => setSelected(p)} className="flex items-center gap-3 text-left">
                      <Avatar name={p.company_name} />
                      <div className="min-w-0">
                        <div className="font-medium text-text">{p.company_name}</div>
                        {p.website && <div className="truncate font-mono text-[11px] text-muted">{host(p.website)}</div>}
                      </div>
                    </button>
                  </TD>
                  <TD className="text-text-2">
                    {[p.industry, p.estimated_stage].filter(Boolean).join(" · ") || "-"}
                  </TD>
                  <TD className="text-text-2">{p.location || "-"}</TD>
                  <TD className="text-right">
                    {pct(p.confidence) ? (
                      <span className="font-mono font-medium text-accent">{pct(p.confidence)}</span>
                    ) : (
                      <span className="text-muted">-</span>
                    )}
                  </TD>
                  <TD className="max-w-[320px] truncate text-muted">{p.why_it_matches || "-"}</TD>
                  <TD className="text-right">
                    <Button variant="secondary" size="sm" onClick={() => setSelected(p)}>
                      <Eye className="size-4" /> Review
                    </Button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </DataTable>
      )}

      <AnimatePresence>
        {selected && <ProspectDrawer prospect={selected} onClose={() => setSelected(null)} />}
      </AnimatePresence>
    </div>
  );
}

function ProspectDrawer({ prospect: p, onClose }: { prospect: DiscoveryProspect; onClose: () => void }) {
  const signals = p.basic_signals || [];
  return (
    <motion.div className="fixed inset-0 z-50" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <button aria-label="Close prospect detail" className="absolute inset-0 bg-black/50" onClick={onClose} />
      <motion.aside
        initial={{ x: 520 }}
        animate={{ x: 0 }}
        exit={{ x: 520 }}
        transition={{ duration: 0.22, ease: "easeOut" }}
        className="glass-panel absolute right-0 top-0 flex h-full w-full max-w-lg flex-col border-l border-border shadow-card"
      >
        <div className="flex items-start justify-between gap-4 border-b border-border p-5">
          <div className="flex items-center gap-3">
            <Avatar name={p.company_name} />
            <div className="min-w-0">
              <div className="text-base font-semibold text-text">{p.company_name}</div>
              {p.website && (
                <a
                  href={p.website.startsWith("http") ? p.website : `https://${p.website}`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 font-mono text-xs text-accent hover:underline"
                >
                  <Globe className="size-3" /> {host(p.website)}
                </a>
              )}
            </div>
          </div>
          <Button variant="ghost" size="icon" aria-label="Close" onClick={onClose}>
            <X className="size-4" />
          </Button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          <Card>
            <CardHeader>
              <CardTitle>Why it matches your ICP</CardTitle>
              {pct(p.confidence) && <Badge tone="accent">{pct(p.confidence)} match</Badge>}
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-text-2">
              <p>{p.why_it_matches || "No match rationale was recorded for this company."}</p>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <Info label="Industry" value={p.industry} />
                <Info label="Stage" value={p.estimated_stage} />
                <Info label="Company size" value={p.estimated_company_size} />
                <Info label="Location" value={p.location} />
              </div>
            </CardContent>
          </Card>

          {signals.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Signals found</CardTitle>
                {p.discovery_source && <Badge tone="neutral">via {p.discovery_source}</Badge>}
              </CardHeader>
              <CardContent className="space-y-2">
                {signals.map((sig, k) => (
                  <div
                    key={k}
                    className="flex items-start gap-2.5 rounded-md border border-border-faint bg-black/[0.02] p-3 text-sm text-text-2"
                  >
                    <Sparkles className="mt-0.5 size-3.5 shrink-0 text-accent" />
                    {sig}
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          {/* The evidence-backed research trail, shown only when this prospect has a
              genuine one (the backend omits it otherwise). Reuses the same component
              as Chat; `live` is false so restored data is never replayed as active. */}
          {p.research_trail && p.research_trail.length > 0 && (
            <div>
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">Research trail</div>
              <ResearchTrail events={p.research_trail} live={false} defaultOpen />
            </div>
          )}

          <Button asChild variant="primary" className="w-full">
            <Link href="/campaigns/new">
              <Plus className="size-4" /> Research &amp; add to a campaign
            </Link>
          </Button>
        </div>
      </motion.aside>
    </motion.div>
  );
}

function Info({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="rounded-md border border-border-faint bg-black/[0.02] p-3">
      <div className="text-muted">{label}</div>
      <div className="mt-1 truncate font-medium text-text">{value || "-"}</div>
    </div>
  );
}
