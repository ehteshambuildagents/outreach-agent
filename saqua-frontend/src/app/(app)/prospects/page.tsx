"use client";

import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, Eye, Plus, Users, X } from "lucide-react";
import { Avatar } from "@/components/ui/avatar";
import { Badge, StatusPill } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { DataTable, Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { prospects } from "@/lib/mock";
import { useState } from "react";

type Prospect = (typeof prospects)[number];

const previews = [
  "Noticed Linear's focus on quality product workflows.",
  "Could tie outreach to Notion's team expansion.",
  "Developer workflow angle around Vercel launch velocity.",
  "Ops automation angle for Retool builders.",
  "Founder-led AI tooling campaign fit.",
  "Database platform use case with dev-first language.",
  "Technical docs and RAG workflow personalization.",
];

export default function ProspectsPage() {
  const [selected, setSelected] = useState<Prospect | null>(null);

  return (
    <div>
      <PageHeader
        title="Prospects"
        subtitle="Discovered and qualified leads ready for campaign review."
        actions={
          <>
            <Badge tone="accent">demo fallback</Badge>
            <Button asChild variant="primary">
              <Link href="/campaigns/new">
                <Plus className="size-4" /> Add to campaign
              </Link>
            </Button>
          </>
        }
      />
      <DataTable>
        <Table>
          <THead>
            <TR>
              <TH>Person</TH>
              <TH>Company</TH>
              <TH>Status</TH>
              <TH className="text-right">Score</TH>
              <TH>Email preview</TH>
              <TH className="text-right">Action</TH>
            </TR>
          </THead>
          <TBody>
            {prospects.length === 0 ? (
              <TR>
                <TD colSpan={6} className="p-5">
                  <EmptyState
                    icon={Users}
                    title="No prospects yet."
                    body="Start a campaign and Saqua will discover qualified people for your ICP."
                    action={
                      <Button asChild variant="primary">
                        <Link href="/campaigns/new">
                          <Plus className="size-4" /> Find prospects
                        </Link>
                      </Button>
                    }
                  />
                </TD>
              </TR>
            ) : (
              prospects.map((person, index) => (
                <TR key={person.name}>
                  <TD>
                    <button onClick={() => setSelected(person)} className="flex items-center gap-3 text-left">
                      <Avatar name={person.name} />
                      <div>
                        <div className="font-medium text-text">{person.name}</div>
                        <div className="text-xs text-muted">{person.role}</div>
                      </div>
                    </button>
                  </TD>
                  <TD>{person.company}</TD>
                  <TD>
                    <StatusPill state={person.status} />
                  </TD>
                  <TD className="text-right font-medium text-text tabular-nums">{person.score}</TD>
                  <TD className="max-w-[320px] truncate text-muted">{previews[index] ?? "Personalized first email ready for review."}</TD>
                  <TD className="text-right">
                    <Button variant="secondary" size="sm" onClick={() => setSelected(person)}>
                      <Eye className="size-4" /> Review
                    </Button>
                  </TD>
                </TR>
              ))
            )}
          </TBody>
        </Table>
      </DataTable>

      <AnimatePresence>{selected && <ProspectDrawer prospect={selected} onClose={() => setSelected(null)} />}</AnimatePresence>
    </div>
  );
}

function ProspectDrawer({ prospect, onClose }: { prospect: Prospect; onClose: () => void }) {
  return (
    <motion.div className="fixed inset-0 z-50" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <button aria-label="Close prospect detail" className="absolute inset-0 bg-black/50" onClick={onClose} />
      <motion.aside
        initial={{ x: 520 }}
        animate={{ x: 0 }}
        exit={{ x: 520 }}
        transition={{ duration: 0.22, ease: "easeOut" }}
        className="absolute right-0 top-0 flex h-full w-full max-w-lg flex-col border-l border-border bg-panel shadow-card"
      >
        <div className="flex items-start justify-between gap-4 border-b border-border p-5">
          <div className="flex items-center gap-3">
            <Avatar name={prospect.name} />
            <div>
              <div className="text-base font-semibold text-text">{prospect.name}</div>
              <div className="text-sm text-muted">{prospect.role} at {prospect.company}</div>
            </div>
          </div>
          <Button variant="ghost" size="icon" aria-label="Close" onClick={onClose}>
            <X className="size-4" />
          </Button>
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          <Card>
            <CardHeader>
              <CardTitle>Research summary</CardTitle>
              <StatusPill state="Qualified" />
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-text-2">
              <p>{prospect.company} matches the ICP because the public site shows founder-led product growth and enough detail for grounded personalization.</p>
              <p className="rounded-md border border-accent-line bg-accent-soft p-3 text-xs text-accent-hi">
                Demo fallback: the backend does not currently expose a saved prospects list endpoint for this page.
              </p>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <Info label="Email" value={prospect.email} />
                <Info label="Location" value={prospect.location} />
                <Info label="Score" value={`${prospect.score}/100`} />
                <Info label="Status" value={prospect.status} />
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Email preview</CardTitle>
              <Badge tone="accent">draft</Badge>
            </CardHeader>
            <CardContent>
              <div className="rounded-md border border-border-faint bg-white/[0.02] p-4 text-sm leading-6 text-text-2">
                Noticed {prospect.company}&apos;s work around product velocity. Saqua could help test a small founder-led outbound motion without generic personalization risk.
              </div>
              <Button asChild variant="primary" className="mt-4 w-full">
                <Link href="/campaigns/new">Add to campaign</Link>
              </Button>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Recent activity</CardTitle>
              <Badge tone="accent">live</Badge>
            </CardHeader>
            <CardContent className="space-y-3">
              {["Research completed", "Qualified for founder-led growth angle", "Draft generated and ready"].map((item) => (
                <div key={item} className="flex items-center gap-3 rounded-md border border-border-faint bg-white/[0.02] p-3 text-sm text-text-2">
                  <CheckCircleMini />
                  {item}
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </motion.aside>
    </motion.div>
  );
}

function Info({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border border-border-faint bg-white/[0.02] p-3">
      <div className="text-muted">{label}</div>
      <div className="mt-1 truncate font-medium text-text">{value}</div>
    </div>
  );
}

function CheckCircleMini() {
  return (
    <span className="grid size-5 shrink-0 place-items-center rounded-full bg-success-soft text-success">
      <CheckCircle2 className="size-3.5" />
    </span>
  );
}
