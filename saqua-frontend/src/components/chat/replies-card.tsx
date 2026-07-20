"use client";

import { motion } from "framer-motion";
import { MailCheck } from "lucide-react";
import { Card } from "@/components/ui/card";
import { timeAgo } from "@/lib/utils";
import type { RepliesCardData } from "@/lib/api";

/**
 * Who has replied across ALL the user's sequences, from `summarize_replies`.
 * Saqua detects that a reply ARRIVED (and auto-stops that sequence) but never
 * stores the reply text — so this shows WHO replied and roughly when, never
 * fabricated contents.
 */
export function RepliesCard({ data }: { data: RepliesCardData }) {
  const replies = data.replies || [];
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
      <Card className="overflow-hidden">
        <div className="flex items-center gap-2 border-b border-border-faint px-5 py-3">
          <MailCheck className="size-4 text-accent" />
          <span className="text-sm font-medium text-text">
            {data.count} {data.count === 1 ? "reply" : "replies"}
          </span>
          <span className="text-xs text-muted">across your campaigns</span>
        </div>

        <div className="divide-y divide-border-faint">
          {replies.map((r, i) => (
            <div key={i} className="flex items-center gap-3 px-5 py-3">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-text">{r.company}</div>
                {r.to && <div className="truncate font-mono text-[11px] text-muted">{r.to}</div>}
              </div>
              <div className="shrink-0 text-right">
                <div className="text-xs text-accent">
                  replied{r.replied_at ? ` ${timeAgo(r.replied_at)}` : ""}
                </div>
                {typeof r.emails_before_reply === "number" && r.emails_before_reply > 0 && (
                  <div className="font-mono text-[11px] text-muted">
                    after {r.emails_before_reply} email{r.emails_before_reply === 1 ? "" : "s"}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="border-t border-border-faint px-5 py-2.5 text-[11px] text-muted">
          Saqua stops a sequence the moment a reply lands — reply to these personally.
        </div>
      </Card>
    </motion.div>
  );
}
