import type { Metadata } from "next";

/**
 * Contact is a client component (it owns the form state), and a client component
 * cannot export `metadata`, so without this layout the page inherited the
 * site-wide title and gave no indication of what it was in a tab or a shared link.
 */
export const metadata: Metadata = {
  title: "Contact · Saqua",
  description: "Get in touch with the team behind Saqua.",
};

export default function ContactLayout({ children }: { children: React.ReactNode }) {
  return children;
}
