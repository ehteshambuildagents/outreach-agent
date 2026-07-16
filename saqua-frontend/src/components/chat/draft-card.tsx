"use client";

import { motion } from "framer-motion";
import { Mail, PanelRight, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import type { EmailCardData } from "@/lib/api";

/**
 * Compact in-conversation representation of a generated email. It does NOT show
 * the body inline — clicking it opens the full draft in the artifact panel beside
 * the conversation. `active` marks the draft currently shown in the panel.
 */
export function DraftCard({
  data,
  active,
  onOpen,
}: {
  data: EmailCardData;
  active: boolean;
  onOpen: () => void;
}) {
  const preview = (data.body || "").replace(/\s+/g, " ").trim().slice(0, 90);
  return (
    <motion.button
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      onClick={onOpen}
      className={cn(
        "group flex w-full items-center gap-3 rounded-lg border bg-card px-4 py-3 text-left transition-colors",
        active ? "border-accent-line bg-accent-soft/40" : "border-border hover:border-border-strong hover:bg-card-2",
      )}
    >
      <span className="grid size-9 shrink-0 place-items-center rounded-md bg-accent-soft text-accent-hi">
        <Mail className="size-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-text">{data.subject || "Draft email"}</span>
          {(data.to || data.company) && (
            <span className="shrink-0 font-mono text-[11px] text-muted">{data.to || data.company}</span>
          )}
        </span>
        {preview && <span className="mt-0.5 block truncate text-xs text-muted">{preview}…</span>}
      </span>
      <span
        className={cn(
          "inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium",
          active ? "text-accent-hi" : "text-text-2 group-hover:text-text",
        )}
      >
        {active ? <Check className="size-3.5" /> : <PanelRight className="size-3.5" />}
        {active ? "Open" : "View draft"}
      </span>
    </motion.button>
  );
}
