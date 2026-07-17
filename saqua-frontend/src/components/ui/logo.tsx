import { cn } from "@/lib/utils";

/**
 * The Saqua brand mark. Renders the asset at /public/saqua-logo.svg so the real
 * logo can be swapped in by replacing that one file — every usage updates at once.
 * The wave keeps its own blue→teal brand colors; the app's green accent is for UI.
 *
 * On hover the mark plays a one-shot "wave wipe": it recedes out to the right and
 * washes back in from the left (keyframe `wave-wipe` in tailwind.config).
 */
export function Logo({ className }: { className?: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src="/saqua-logo.svg"
      alt="Saqua"
      draggable={false}
      className={cn("block select-none hover:animate-wave-wipe", className)}
    />
  );
}
