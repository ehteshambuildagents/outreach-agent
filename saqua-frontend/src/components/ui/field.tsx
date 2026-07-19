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
        "h-10 w-full rounded-lg border border-border bg-card px-3 text-sm text-text shadow-card outline-none transition-colors placeholder:text-muted focus:border-accent focus:ring-2 focus:ring-accent-soft",
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
        "min-h-[180px] w-full resize-none rounded-lg border border-border bg-card p-3 text-sm leading-6 text-text shadow-card outline-none transition-colors placeholder:text-muted focus:border-accent focus:ring-2 focus:ring-accent-soft",
        props.className,
      )}
    />
  );
}
