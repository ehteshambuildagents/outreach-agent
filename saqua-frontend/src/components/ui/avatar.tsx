import { cn, initials } from "@/lib/utils";

export function Avatar({ name, className }: { name: string; className?: string }) {
  return (
    <div
      className={cn(
        "grid size-9 place-items-center rounded-full border border-accent-line bg-accent-soft text-xs font-semibold text-accent-hi",
        className,
      )}
    >
      {initials(name)}
    </div>
  );
}
