import * as React from "react";
import { cn } from "@/lib/utils";

export function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("block space-y-2", className)}>
      <span className="text-xs font-medium text-text-2">{label}</span>
      {children}
    </label>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cn(
        "h-10 w-full rounded-sm border border-border bg-white/[0.03] px-3 text-sm text-text outline-none transition-colors placeholder:text-muted focus:border-border-strong focus:bg-white/[0.05]",
        props.className,
      )}
    />
  );
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cn(
        "min-h-[180px] w-full resize-none rounded-sm border border-border bg-white/[0.03] p-3 text-sm leading-6 text-text outline-none transition-colors placeholder:text-muted focus:border-border-strong focus:bg-white/[0.05]",
        props.className,
      )}
    />
  );
}
