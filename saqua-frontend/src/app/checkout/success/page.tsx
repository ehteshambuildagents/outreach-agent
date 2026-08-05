import Link from "next/link";
import { ArrowRight, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SiteNav } from "@/components/marketing/site-nav";
import { SiteFooter } from "@/components/marketing/site-footer";

export const metadata = {
  title: "Payment received · Saqua",
  description: "Your subscription is active.",
};

/**
 * Post-payment confirmation. Lemon Squeezy returns the browser here after a
 * successful hosted checkout (BILLING_SUCCESS_URL), so it is a PUBLIC route
 * (see middleware): it must render even before the buyer's session claims and the
 * webhook-granted approval have fully propagated. It exposes no account data — it
 * only confirms the charge and hands off to the dashboard.
 *
 * By the time someone lands here the Lemon Squeezy webhook has recorded their
 * subscription and approved the account, so "Continue to dashboard" reaches the
 * product. If the webhook is momentarily behind, the dashboard's own loading and
 * the backend gate handle it gracefully — no data is shown until the account is
 * approved.
 */
export default function CheckoutSuccessPage() {
  return (
    <main className="relative min-h-screen overflow-clip text-text">
      <div aria-hidden className="hero-glow pointer-events-none absolute inset-x-0 top-0 -z-10 h-[600px]" />

      <SiteNav />

      <section className="mx-auto flex max-w-xl flex-col items-center px-6 pb-24 pt-40 text-center">
        <span className="inline-flex size-14 items-center justify-center rounded-full bg-accent/10 text-accent">
          <CheckCircle2 className="size-8" />
        </span>

        <h1 className="mt-7 font-display text-4xl font-medium tracking-[-0.03em] md:text-5xl">
          Payment received.
        </h1>

        <p className="mx-auto mt-5 max-w-md text-base leading-7 text-muted">
          Thank you. Your subscription is active and your prospect allowance is ready. You can head
          into Saqua and start your first campaign now. A receipt is on its way to your email.
        </p>

        <div className="mt-9 flex flex-col items-center gap-3 sm:flex-row">
          <Button asChild variant="primary" size="lg">
            <Link href="/dashboard">
              Continue to dashboard <ArrowRight className="size-4" />
            </Link>
          </Button>
          <Button asChild variant="ghost" size="lg">
            <Link href="/settings">Manage billing</Link>
          </Button>
        </div>

        <p className="mt-8 text-xs text-muted">
          Need help? <Link className="underline" href="/contact">Talk to us</Link>.
        </p>
      </section>

      <SiteFooter />
    </main>
  );
}
