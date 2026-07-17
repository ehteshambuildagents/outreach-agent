"use client";

import { motion } from "framer-motion";
import { Send, MailCheck, Percent, Radio, Pause, Megaphone, type LucideIcon } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn, fmtNumber, fmtPct } from "@/lib/utils";
import type { StatsCardData } from "@/lib/api";

/**
 * The user's REAL outreach analytics from `get_stats` — computed live from their
 * own automation data, never fabricated. Same visual language as the dashboard
 * stat tiles (Geist-Mono numbers; the single green accent marks live/positive
 * signals — replies, reply rate, active sequences).
 */
export function StatsCard({ data }: { data: StatsCardData }) {
  const tiles: { label: string; value: string; icon: LucideIcon; accent?: boolean }[] = [
    { label: "Emails sent", value: fmtNumber(data.emails_sent), icon: Send },
    { label: "Replies", value: fmtNumber(data.replies), icon: MailCheck, accent: data.replies > 0 },
    { label: "Reply rate", value: fmtPct(data.reply_rate, 0), icon: Percent, accent: data.reply_rate > 0 },
    { label: "Active", value: fmtNumber(data.sequences_active), icon: Radio, accent: data.sequences_active > 0 },
    { label: "Paused", value: fmtNumber(data.sequences_paused), icon: Pause },
    { label: "Campaigns", value: fmtNumber(data.campaigns), icon: Megaphone },
  ];

  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
      <Card className="overflow-hidden">
        <div className="border-b border-border-faint px-5 py-3 text-sm font-medium text-text">Your outreach</div>
        <div className="grid grid-cols-2 gap-2 p-4 sm:grid-cols-3">
          {tiles.map((t) => (
            <div key={t.label} className="rounded-md border border-border-faint bg-white/[0.02] px-3 py-2.5">
              <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-muted">
                <t.icon className={cn("size-3.5", t.accent ? "text-accent-hi" : "text-muted")} />
                {t.label}
              </div>
              <div className={cn("mt-1 font-mono text-lg font-semibold tracking-tight", t.accent ? "text-accent-hi" : "text-text")}>
                {t.value}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </motion.div>
  );
}
