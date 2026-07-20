import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";

export function EmptyState({
  icon: Icon,
  title,
  body,
  action,
}: {
  icon: LucideIcon;
  title: string;
  body: string;
  action: ReactNode;
}) {
  return (
    <Card className="overflow-hidden border-dashed">
      <div className="relative p-8 text-center">
        <div className="accent-glow absolute inset-0 opacity-40" />
        <div className="relative mx-auto mb-4 grid size-12 place-items-center rounded-xl border border-accent-line bg-accent-soft text-accent shadow-glow">
          <Icon className="size-5" />
        </div>
        <div className="relative text-sm font-semibold text-text">{title}</div>
        <p className="relative mx-auto mt-2 max-w-sm text-sm leading-6 text-muted">{body}</p>
        <div className="relative mt-5">{action}</div>
      </div>
    </Card>
  );
}
