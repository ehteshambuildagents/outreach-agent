"use client";

import { useState } from "react";
import { Copy, Check, ShieldCheck, Send } from "lucide-react";
import { motion } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ChannelCardData } from "@/lib/api";

const GUARD_TONE = { ALLOW: "success", WARN: "warn", BLOCK: "danger" } as const;

/** A safe-channel draft (X / Reddit / HN reply, contact form) from
 * `draft_channel_message`. Draft ONLY: there is deliberately NO send/post button —
 * `posted` is always false from the backend. The user copies it and posts it
 * manually, then can mark it posted locally. */
export function ChannelCard({ data }: { data: ChannelCardData }) {
  const [copied, setCopied] = useState(false);
  const [posted, setPosted] = useState(Boolean(data.posted));

  async function copy() {
    try {
      await navigator.clipboard.writeText(data.body || "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard can be blocked (no HTTPS / permissions) — fail quietly.
    }
  }

  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-border-faint px-5 py-3">
          <Badge tone="accent" dot>
            {data.label || data.channel}
          </Badge>
          {data.company && <span className="text-xs text-muted">for {data.company}</span>}
          <div className="ml-auto flex items-center gap-2">
            {data.guard && (
              <Badge tone={GUARD_TONE[data.guard] ?? "neutral"}>
                <ShieldCheck className="size-3" /> {data.guard}
              </Badge>
            )}
            {typeof data.char_count === "number" && (
              <span className="text-[11px] text-muted">{data.char_count} chars</span>
            )}
          </div>
        </div>

        <div className="px-5 py-4">
          <p className="whitespace-pre-wrap text-sm leading-6 text-text-2">{data.body}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-border-faint px-5 py-3">
          <Button size="sm" variant="secondary" onClick={copy}>
            {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
            {copied ? "Copied" : "Copy"}
          </Button>
          <Button size="sm" variant={posted ? "ghost" : "outline"} onClick={() => setPosted((v) => !v)}>
            {posted ? <Check className="size-3.5" /> : <Send className="size-3.5" />}
            {posted ? "Marked as posted" : "Mark as posted"}
          </Button>
          <span className="ml-auto text-[11px] text-muted">Draft only — post it manually.</span>
        </div>
      </Card>
    </motion.div>
  );
}
