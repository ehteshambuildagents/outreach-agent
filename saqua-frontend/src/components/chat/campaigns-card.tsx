"use client";

import { motion } from "framer-motion";
import { Megaphone } from "lucide-react";
import { Card } from "@/components/ui/card";
import { StatusPill } from "@/components/ui/badge";
import { timeAgo } from "@/lib/utils";
import type { CampaignsCardData } from "@/lib/api";

/**
 * The user's campaigns and where each stands, from `list_campaigns`. Display
 * only — the agent pauses/launches via its tools; this just shows the real state
 * so the user (and the agent) can decide what to do next.
 */
export function CampaignsCard({ data }: { data: CampaignsCardData }) {
  const campaigns = data.campaigns || [];
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
      <Card className="overflow-hidden">
        <div className="flex items-center gap-2 border-b border-border-faint px-5 py-3">
          <Megaphone className="size-4 text-accent" />
          <span className="text-sm font-medium text-text">
            {data.count} campaign{data.count === 1 ? "" : "s"}
          </span>
        </div>

        <div className="divide-y divide-border-faint">
          {campaigns.map((c) => (
            <div key={c.id} className="flex items-center gap-3 px-5 py-3">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-text">{c.name}</div>
                {c.updated_at ? <div className="text-[11px] text-muted">{timeAgo(c.updated_at)}</div> : null}
              </div>
              {c.launched > 0 && (
                <span className="shrink-0 font-mono text-[11px] text-accent">{c.launched} live</span>
              )}
              {c.status && <StatusPill state={c.status} />}
            </div>
          ))}
        </div>
      </Card>
    </motion.div>
  );
}
