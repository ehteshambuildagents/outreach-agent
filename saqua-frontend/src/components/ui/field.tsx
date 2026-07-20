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
        "h-10 w-full rounded-sm border border-border-strong bg-white px-3 text-sm text-text shadow-[0_1px_2px_rgba(17,17,17,.04)] outline-none transition-all placeholder:text-faint focus:border-accent-line focus:shadow-[0_0_0_4px_var(--accent-soft)]",
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
        "min-h-[180px] w-full resize-none rounded-sm border border-border-strong bg-white p-3 text-sm leading-6 text-text shadow-[0_1px_2px_rgba(17,17,17,.04)] outline-none transition-all placeholder:text-faint focus:border-accent-line focus:shadow-[0_0_0_4px_var(--accent-soft)]",
        props.className,
      )}
    />
  );
}
