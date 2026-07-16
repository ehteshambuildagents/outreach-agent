import { Lock } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A real browser window chrome — Saqua is a web product, not a desktop app, so
 * product UI shown inside marketing/dashboard content gets muted (not colorful)
 * traffic dots plus a real address-bar pill showing the actual path.
 */
export function BrowserFrame({
  url,
  children,
  className,
}: {
  url: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("overflow-hidden rounded-xl border border-border bg-card shadow-pop", className)}>
      <div className="flex items-center gap-3 border-b border-border-faint bg-panel/70 px-3.5 py-2.5">
        <div className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-white/15" />
          <span className="size-2.5 rounded-full bg-white/15" />
          <span className="size-2.5 rounded-full bg-white/15" />
        </div>
        <div className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md border border-border-faint bg-white/[0.02] px-2.5 py-1">
          <Lock className="size-3 shrink-0 text-muted" />
          <span className="truncate font-mono text-[11px] text-text-2">{url}</span>
        </div>
      </div>
      <div className="bg-bg">{children}</div>
    </div>
  );
}
