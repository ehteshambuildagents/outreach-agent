import type { Metadata } from "next";

/**
 * Pricing is a client component (the billing toggle holds state), and a client
 * component cannot export `metadata`. Without this layout the page inherited the
 * site-wide title, so a shared /pricing link and its browser tab both read
 * "Saqua - researched outbound for founders" with no hint of what the page is.
 */
export const metadata: Metadata = {
  title: "Pricing · Saqua",
  description:
    "Priced per prospect Saqua actually researches, not per seat. Every plan runs the full pipeline: research, scoring, writing, follow-ups, and reply detection.",
};

export default function PricingLayout({ children }: { children: React.ReactNode }) {
  return children;
}
