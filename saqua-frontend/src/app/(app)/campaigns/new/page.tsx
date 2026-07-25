"use client";

import { useState } from "react";
import Link from "next/link";
import {
  Rocket,
  Radar,
  Search,
  Target,
  Sparkles,
  PenLine,
  ShieldCheck,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  ArrowRight,
  X,
  UserPlus,
} from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, Input, Textarea } from "@/components/ui/field";
import { PageHeader } from "@/components/ui/page-header";
import { SummaryGrid } from "@/components/campaign/summary-grid";
import { ProspectCard } from "@/components/campaign/prospect-card";
import { api, type CampaignDetail, type CampaignPreviewProspect } from "@/lib/api";
import { cn } from "@/lib/utils";

type Phase = "form" | "running" | "results" | "launched" | "error";
type Provider = "dryrun" | "gmail" | "outlook";

const PIPELINE = [
  { label: "Discovery", icon: Radar },
  { label: "Research", icon: Search },
  { label: "Qualification", icon: Target },
  { label: "Strategy", icon: Sparkles },
  { label: "Writer", icon: PenLine },
  { label: "Guard", icon: ShieldCheck },
];

function parseList(v: string): string[] {
  return v
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function NewCampaignPage() {
  const [phase, setPhase] = useState<Phase>("form");
  const [error, setError] = useState("");
  const [campaign, setCampaign] = useState<CampaignDetail | null>(null);
  const [provider, setProvider] = useState<Provider>("dryrun");
  const [launchError, setLaunchError] = useState("");
  const [launching, setLaunching] = useState(false);
  const [recipientProspect, setRecipientProspect] = useState<CampaignPreviewProspect | null>(null);
  const [recipientName, setRecipientName] = useState("");
  const [recipientEmail, setRecipientEmail] = useState("");
  const [recipientError, setRecipientError] = useState("");
  const [savingRecipient, setSavingRecipient] = useState(false);

  const [name, setName] = useState("");
  const [raw, setRaw] = useState("");
  const [industry, setIndustry] = useState("");
  const [location, setLocation] = useState("");
  const [employees, setEmployees] = useState("");
  const [funding, setFunding] = useState("");
  const [keywords, setKeywords] = useState("");
  const [exclude, setExclude] = useState("");
  const [limit, setLimit] = useState(3);

  const canSubmit = name.trim().length > 0 && (raw.trim().length > 0 || industry.trim().length > 0);

  async function runOrchestration() {
    setError("");
    setLaunchError("");
    setPhase("running");
    const res = await api.createCampaign({
      name: name.trim() || "New campaign",
      limit,
      icp: {
        raw: raw.trim(),
        industry: industry.trim(),
        location: location.trim(),
        employee_range: employees.trim(),
        funding_stage: funding.trim(),
        keywords: parseList(keywords),
        exclude_keywords: parseList(exclude),
      },
    });
    if (!res.ok) {
      const unreachable = /^HTTP 5\d\d$/.test(res.error) || res.error === "network error";
      setError(
        unreachable
          ? `Couldn't reach the campaign service (${res.error}). Make sure the backend is running, then retry.`
          : res.error || "Orchestration failed.",
      );
      setPhase("error");
      return;
    }
    setCampaign(res.data);
    setPhase("results");
  }

  async function launch() {
    if (!campaign) return;
    setLaunchError("");
    setLaunching(true);
    const res = await api.launchCampaign(campaign.id, provider);
    setLaunching(false);
    if (!res.ok) {
      setLaunchError(
        res.status === 400
          ? "No guard-approved emails with a valid recipient address. Nothing was launched."
          : `Launch failed: ${res.error}`,
      );
      return;
    }
    setCampaign(res.data);
    setPhase("launched");
  }

  function openRecipientEditor(prospect: CampaignPreviewProspect) {
    const recipient = prospect.email?.recipient || prospect.research?.recipient || prospect.manual_recipient;
    setRecipientProspect(prospect);
    setRecipientName(recipient?.name || prospect.research?.named_person || "");
    setRecipientEmail(recipient?.email || "");
    setRecipientError("");
  }

  async function saveRecipient() {
    if (!campaign || !recipientProspect) return;
    const domain = recipientProspect.domain;
    const email = recipientEmail.trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setRecipientError("Enter a valid recipient email address.");
      return;
    }
    setSavingRecipient(true);
    setRecipientError("");
    const res = await api.updateCampaignRecipient(campaign.id, domain, {
      name: recipientName.trim(),
      email,
    });
    setSavingRecipient(false);
    if (!res.ok) {
      setRecipientError(res.error || "Could not update this recipient.");
      return;
    }
    setCampaign(res.data);
    setRecipientProspect(null);
    setRecipientName("");
    setRecipientEmail("");
    setLaunchError("");
  }

  const summary = campaign?.result?.summary ?? campaign?.summary;
  const prospects = campaign?.result?.prospects ?? [];
  const launchable = summary?.launchable ?? 0;
  const blockedReason = campaign?.result?.reason || "";

  return (
    <div>
      <PageHeader
        title="New campaign"
        subtitle="Describe your ICP. Saqua discovers, researches, qualifies, writes, and guards every email before anything can launch."
        actions={
          phase === "results" || phase === "launched" ? (
            <Button
              variant="secondary"
              onClick={() => {
                setPhase("form");
                setCampaign(null);
              }}
            >
              <RefreshCw className="size-4" /> New search
            </Button>
          ) : undefined
        }
      />

      {phase === "form" && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
          <Card>
            <CardHeader>
              <CardTitle>Ideal customer profile</CardTitle>
              <span className="text-xs text-muted">Saqua runs the full pipeline on each prospect</span>
            </CardHeader>
            <CardContent className="grid gap-5">
              <Field label="Campaign name">
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="SaaS Founders, US" />
              </Field>
              <Field label="Describe your ICP">
                <Textarea
                  value={raw}
                  onChange={(e) => setRaw(e.target.value)}
                  placeholder="B2B SaaS companies building developer tools, recently raised a seed or Series A, 5-50 employees, US-based."
                  className="min-h-[110px]"
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Industry">
                  <Input value={industry} onChange={(e) => setIndustry(e.target.value)} placeholder="Developer tools" />
                </Field>
                <Field label="Location">
                  <Input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="United States" />
                </Field>
                <Field label="Company size">
                  <Input value={employees} onChange={(e) => setEmployees(e.target.value)} placeholder="5-50" />
                </Field>
                <Field label="Funding stage">
                  <Input value={funding} onChange={(e) => setFunding(e.target.value)} placeholder="Seed / Series A" />
                </Field>
                <Field label="Keywords (comma separated)">
                  <Input value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="api, infra, open source" />
                </Field>
                <Field label="Exclude keywords">
                  <Input value={exclude} onChange={(e) => setExclude(e.target.value)} placeholder="agency, staffing" />
                </Field>
              </div>
              <div className="flex flex-wrap items-end justify-between gap-4 border-t border-border-faint pt-4">
                <Field label={`Prospects to process (${limit})`} className="max-w-xs">
                  <input
                    type="range"
                    min={1}
                    max={10}
                    value={limit}
                    onChange={(e) => setLimit(Number(e.target.value))}
                    className="mt-2 w-full accent-[color:var(--accent)]"
                  />
                  <span className="text-[11px] text-muted">Research runs live, so each prospect adds ~1-2 min.</span>
                </Field>
                <Button variant="primary" size="lg" disabled={!canSubmit} onClick={runOrchestration}>
                  <Sparkles className="size-4" /> Run Saqua
                </Button>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}

      {phase === "running" && <RunningState />}

      {phase === "error" && (
        <EmptyState
          icon={AlertTriangle}
          title="Orchestration failed"
          body={error || "Saqua could not reach the campaign backend."}
          action={
            <Button variant="primary" onClick={runOrchestration}>
              <RefreshCw className="size-4" /> Retry
            </Button>
          }
        />
      )}

      {(phase === "results" || phase === "launched") && campaign && (
        <div className="space-y-4">
          {phase === "launched" && (
            <Card className="border-[color:var(--success-soft)] bg-success-soft/40">
              <CardContent className="flex flex-wrap items-center gap-3 py-4">
                <CheckCircle2 className="size-5 text-success" />
                <div className="flex-1">
                  <div className="text-sm font-medium text-text">
                    Launched: {campaign.workflow_ids.length} workflow{campaign.workflow_ids.length === 1 ? "" : "s"} created
                    via {provider}.
                  </div>
                  <div className="text-xs text-muted">Follow-ups stop automatically when a prospect replies.</div>
                </div>
                <Button asChild variant="secondary" size="sm">
                  <Link href="/automation">
                    View automation <ArrowRight className="size-3.5" />
                  </Link>
                </Button>
              </CardContent>
            </Card>
          )}

          <SummaryGrid summary={summary} />

          {blockedReason && (
            <Card className="border-[color:var(--warn-soft)]">
              <CardContent className="flex items-center gap-3 py-4 text-sm text-text-2">
                <AlertTriangle className="size-4 text-warn" />
                {blockedReason}
              </CardContent>
            </Card>
          )}

          <div className="grid gap-3">
            {prospects.length === 0 && !blockedReason && (
              <EmptyState
                icon={Radar}
                title="No prospects returned"
                body="Discovery did not return usable companies for this ICP. Try broadening the description or keywords."
                action={
                  <Button variant="secondary" onClick={() => setPhase("form")}>
                    Edit ICP
                  </Button>
                }
              />
            )}
            {prospects.map((p, i) => (
              <ProspectCard key={p.domain || i} prospect={p} index={i} onAddRecipient={openRecipientEditor} />
            ))}
          </div>

          {phase !== "launched" && (
            <Card>
              <CardContent className="flex flex-wrap items-center justify-between gap-4 py-4">
                <div>
                  <div className="text-sm font-medium text-text">
                    {launchable > 0
                      ? `${launchable} prospect${launchable === 1 ? "" : "s"} ready to launch`
                      : "No launchable prospects found"}
                  </div>
                  <div className="text-xs text-muted">
                    {launchable > 0
                      ? "Only guard-approved emails with a valid recipient address are launched."
                      : "Every email was blocked, held, or is route-only. Nothing will be sent."}
                  </div>
                  {prospects.filter((p) => p.cadence_warning).length > 0 && (
                    <div className="mt-1.5 text-xs text-warn">
                      {prospects.filter((p) => p.cadence_warning).length} prospect
                      {prospects.filter((p) => p.cadence_warning).length === 1 ? "" : "s"} will launch
                      with a partial 3-step cadence (no mid-sequence follow-ups). See the flagged
                      cards above.
                    </div>
                  )}
                  {launchError && <div className="mt-1.5 text-xs text-danger">{launchError}</div>}
                </div>
                <div className="flex items-center gap-2">
                  <ProviderSelect value={provider} onChange={setProvider} />
                  <Button variant="primary" disabled={launchable === 0 || launching} onClick={launch}>
                    {launching ? <Loader2 className="size-4 animate-spin" /> : <Rocket className="size-4" />}
                    {launching ? "Launching…" : "Launch campaign"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      <AnimatePresence>
        {recipientProspect && (
          <ManualRecipientModal
            prospect={recipientProspect}
            name={recipientName}
            email={recipientEmail}
            error={recipientError}
            saving={savingRecipient}
            onName={setRecipientName}
            onEmail={setRecipientEmail}
            onClose={() => {
              if (!savingRecipient) setRecipientProspect(null);
            }}
            onSave={saveRecipient}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

function ManualRecipientModal({
  prospect,
  name,
  email,
  error,
  saving,
  onName,
  onEmail,
  onClose,
  onSave,
}: {
  prospect: CampaignPreviewProspect;
  name: string;
  email: string;
  error: string;
  saving: boolean;
  onName: (v: string) => void;
  onEmail: (v: string) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const recipient = prospect.email?.recipient || prospect.research?.recipient || {};
  const company = prospect.company_name || prospect.research?.company_name || prospect.domain;
  const source = recipient.contact_page || recipient.linkedin || recipient.route || prospect.website || prospect.domain;
  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <motion.div
        initial={{ opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 10, scale: 0.98 }}
        transition={{ duration: 0.18 }}
        className="w-full max-w-lg rounded-lg border border-border bg-panel p-5 shadow-pop"
      >
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-text">
              <UserPlus className="size-4 text-accent" />
              Add recipient
            </div>
            <p className="mt-1 text-xs leading-5 text-muted">
              Add a verified email for {company}. Saqua will re-check Guard before this prospect becomes launchable.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="rounded-sm p-1.5 text-muted transition-colors hover:bg-black/[0.05] hover:text-text"
            aria-label="Close recipient editor"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="mb-4 rounded-md border border-border-faint bg-black/[0.02] p-3 text-xs text-text-2">
          <div className="font-medium text-text">{company}</div>
          <div className="mt-1 text-muted">Found person: {recipient.name || prospect.research?.named_person || "Not found"}</div>
          <div className="mt-1 break-all text-muted">Route/source: {source || "No route found"}</div>
        </div>

        <div className="space-y-4">
          <Field label="Recipient name">
            <Input value={name} onChange={(e) => onName(e.target.value)} placeholder="Ada Lane" />
          </Field>
          <Field label="Recipient email">
            <Input
              value={email}
              onChange={(e) => onEmail(e.target.value)}
              placeholder="ada@company.com"
              inputMode="email"
              autoComplete="email"
            />
          </Field>
          {error && <div className="rounded-sm border border-danger-soft bg-danger-soft px-3 py-2 text-xs text-danger">{error}</div>}
        </div>

        <div className="mt-5 flex items-center justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onSave} disabled={saving}>
            {saving ? <Loader2 className="size-4 animate-spin" /> : <ShieldCheck className="size-4" />}
            {saving ? "Checking Guard..." : "Save and re-check"}
          </Button>
        </div>
      </motion.div>
    </motion.div>
  );
}

function RunningState() {
  return (
    <Card className="overflow-hidden">
      <div className="relative p-10 text-center">
        <div className="accent-glow absolute inset-0 opacity-40" />
        <Loader2 className="relative mx-auto mb-4 size-7 animate-spin text-accent" />
        <div className="relative text-sm font-semibold text-text">Saqua is working through your prospects…</div>
        <p className="relative mx-auto mt-1.5 max-w-md text-xs leading-5 text-muted">
          Discovery → Research → Qualification → Strategy → Writer → Guard. This runs live on real websites, so it can take a
          few minutes.
        </p>
        <div className="relative mx-auto mt-6 flex max-w-lg flex-wrap items-center justify-center gap-2">
          {PIPELINE.map((s, i) => (
            <motion.div
              key={s.label}
              initial={{ opacity: 0.35 }}
              animate={{ opacity: [0.35, 1, 0.35] }}
              transition={{ duration: 1.6, repeat: Infinity, delay: i * 0.22 }}
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-black/[0.03] px-3 py-1 text-[11px] text-text-2"
            >
              <s.icon className="size-3.5 text-accent" />
              {s.label}
            </motion.div>
          ))}
        </div>
      </div>
    </Card>
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
