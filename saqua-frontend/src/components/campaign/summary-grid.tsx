import { Card } from "@/components/ui/card";
import { cn, fmtCurrency } from "@/lib/utils";
import type { CampaignSummary } from "@/lib/api";

/** The campaign preview scoreboard (reference-style stat cards). Shared by the
 * New Campaign flow and the Campaign Detail page so counts render identically. */
export function SummaryGrid({ summary }: { summary?: CampaignSummary }) {
  const items: { label: string; value: string; tone?: "success" | "danger" | "accent" }[] = [
    { label: "Discovered", value: String(summary?.discovered ?? 0) },
    { label: "Researched", value: String(summary?.research_ok ?? 0) },
    { label: "Qualified", value: String(summary?.qualified ?? 0) },
    { label: "Emails", value: String(summary?.emails_generated ?? 0) },
    { label: "Guard allowed", value: String(summary?.guard_allowed ?? 0), tone: "success" },
    { label: "Blocked", value: String(summary?.guard_blocked ?? 0), tone: "danger" },
    { label: "Launchable", value: String(summary?.launchable ?? 0), tone: "accent" },
    { label: "Est. cost", value: fmtCurrency(summary?.estimated_cost, 2) },
    { label: "Est. latency", value: `${Math.round(summary?.latency_seconds ?? 0)}s` },
  ];
  return (
    <div className="grid grid-cols-3 gap-3 md:grid-cols-5 lg:grid-cols-9">
      {items.map((it) => (
        <Card key={it.label} className="px-3.5 py-3">
          <div className="text-[10px] font-medium uppercase tracking-wide text-muted">{it.label}</div>
          <div
            className={cn(
              "mt-1 text-lg font-semibold tracking-tight",
              it.tone === "success" && "text-success",
              it.tone === "danger" && "text-danger",
              it.tone === "accent" && "text-accent-hi",
              !it.tone && "text-text",
            )}
          >
            {it.value}
          </div>
        </Card>
      ))}
    </div>
  );
}
