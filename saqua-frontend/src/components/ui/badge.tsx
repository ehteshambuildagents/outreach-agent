import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-2xs font-medium whitespace-nowrap",
  {
    variants: {
      tone: {
        neutral: "border-border bg-white/[0.04] text-text-2",
        accent: "border-accent-line bg-accent-soft text-accent-hi",
        success: "border-[color:var(--success-soft)] bg-success-soft text-success",
        warn: "border-[color:var(--warn-soft)] bg-warn-soft text-warn",
        danger: "border-[color:var(--danger-soft)] bg-danger-soft text-danger",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {
  dot?: boolean;
}

export function Badge({ className, tone, dot, children, ...props }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ tone }), className)} {...props}>
      {dot && <span className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

/** Status pill — maps a workflow/campaign state string to a toned pill (reference screens 2-5). */
const STATE_TONE: Record<string, "neutral" | "accent" | "success" | "warn" | "danger"> = {
  active: "success",
  sent: "success",
  replied: "success",
  completed: "neutral",
  paused: "warn",
  waiting: "warn",
  queued: "accent",
  sending: "accent",
  running: "accent",
  failed: "danger",
  cancelled: "danger",
  stopped: "danger",
  bounced: "danger",
  contacted: "accent",
  qualified: "success",
  "not contacted": "neutral",
  "not interested": "danger",
  allow: "success",
  warn: "warn",
  block: "danger",
};

export function StatusPill({ state, className }: { state: string; className?: string }) {
  const key = (state || "").toLowerCase();
  const tone = STATE_TONE[key] ?? "neutral";
  const label = state.charAt(0).toUpperCase() + state.slice(1).toLowerCase();
  return (
    <Badge tone={tone} dot className={className}>
      {label}
    </Badge>
  );
}
