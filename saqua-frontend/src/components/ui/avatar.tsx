import { cn, initials } from "@/lib/utils";

export function Avatar({ name, className }: { name: string; className?: string }) {
  return (
    <div
      className={cn(
        "grid size-9 place-items-center rounded-full border border-white/10 bg-gradient-to-br from-accent to-[#5b3fd6] text-xs font-semibold text-white shadow-card",
        className,
      )}
    >
      {initials(name)}
    </div>
  );
}
