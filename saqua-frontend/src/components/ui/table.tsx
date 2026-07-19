import * as React from "react";
import { cn } from "@/lib/utils";

export function DataTable({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("overflow-x-auto rounded-lg border border-border bg-card", className)} {...props} />
  );
}

export function Table({ className, ...props }: React.TableHTMLAttributes<HTMLTableElement>) {
  return <table className={cn("w-full min-w-[760px] border-collapse text-left text-sm", className)} {...props} />;
}

export function THead({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <thead
      className={cn(
        "border-b border-border bg-card-2 text-2xs font-medium uppercase tracking-wide text-muted",
        className,
      )}
      {...props}
    />
  );
}

export function TBody({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn("divide-y divide-border-faint", className)} {...props} />;
}

export function TR({ className, ...props }: React.HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn("transition-colors hover:bg-hover", className)} {...props} />;
}

export function TH({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn("px-4 py-3 font-medium", className)} {...props} />;
}

export function TD({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-4 py-3 text-text-2", className)} {...props} />;
}
