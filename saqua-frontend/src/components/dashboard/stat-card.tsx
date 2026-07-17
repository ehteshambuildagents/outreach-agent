"use client";

import { ArrowUpRight, ArrowDownRight, type LucideIcon } from "lucide-react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";

export interface StatCardProps {
  label: string;
  value: string;
  delta?: number; // fractional change over range, e.g. 0.26 => +26%
  deltaLabel?: string;
  icon?: LucideIcon;
  index?: number;
}

export function StatCard({ label, value, delta, deltaLabel = "vs last 7 days", icon: Icon, index = 0 }: StatCardProps) {
  const up = (delta ?? 0) >= 0;
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: index * 0.05, ease: [0.22, 0.61, 0.36, 1] }}
    >
      <Card className="group p-5">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-text-2">{label}</span>
          {Icon && (
            <span className="grid size-7 place-items-center rounded-md bg-white/[0.04] text-muted transition-colors group-hover:text-accent-hi">
              <Icon className="size-4" />
            </span>
          )}
        </div>
        <div className="mt-3 text-[28px] font-semibold leading-none tracking-tight text-text">{value}</div>
        {delta != null && (
          <div className="mt-2 flex items-center gap-1.5 text-xs">
            <span className={cn("inline-flex items-center gap-0.5 font-medium", up ? "text-success" : "text-danger")}>
              {up ? <ArrowUpRight className="size-3.5" /> : <ArrowDownRight className="size-3.5" />}
              {up ? "+" : ""}
              {Math.round((delta ?? 0) * 100)}%
            </span>
            <span className="text-muted">{deltaLabel}</span>
          </div>
        )}
      </Card>
    </motion.div>
  );
}
