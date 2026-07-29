"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Building2, CreditCard, Mail, Plug, Save, Settings2, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, Input } from "@/components/ui/field";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useUser } from "@clerk/nextjs";
import { api, type Billing, type CompanyProfile, type OAuthAccount } from "@/lib/api";
import { useDemo } from "@/components/demo/demo-provider";

const emptyCompany: CompanyProfile = {
  name: "",
  website: "",
  one_liner: "",
  audience: "",
  value_prop: "",
  tone: "",
};

const companyFields: { key: keyof CompanyProfile; label: string; placeholder: string }[] = [
  { key: "name", label: "Company name", placeholder: "Acme, Inc." },
  { key: "website", label: "Website", placeholder: "acme.com" },
  { key: "one_liner", label: "What you do", placeholder: "One line on what your company does" },
  { key: "audience", label: "Who you serve", placeholder: "Your ideal customers / ICP" },
  { key: "value_prop", label: "Value / edge", placeholder: "What makes you different" },
  { key: "tone", label: "Preferred tone", placeholder: "e.g. warm, direct, technical" },
];

const mailboxProviders = [
  ["gmail", "Gmail", "Send, receive, and stop sequences on reply"],
  ["outlook", "Outlook", "Connect Microsoft inboxes for sending"],
] as const;

// Self-serve, checkout-able tiers (Enterprise is sales-assisted). Ids are the
// canonical plan ids the API reports (pro/max); prospect counts must match the
// backend PLAN_LIMITS so the card and the enforced cap agree. (The public /pricing
// page still markets these as Starter/Growth; the backend accepts both.)
const planTiers = [
  { id: "pro", name: "Pro", prospects: 50, price: 65 },
  { id: "max", name: "Max", prospects: 100, price: 100 },
] as const;

export default function SettingsPage() {
  const { isDemo } = useDemo();
  const { user, isLoaded } = useUser();
  // Every /api call attaches the Clerk session token from window.Clerk. On first
  // mount that session may not have hydrated yet, so a fetch fired too early goes
  // out with no Authorization header and comes back 401 — which is why the plan
  // card used to hang on "Loading…" and Company details showed "Please sign in".
  // Gate all data fetches on Clerk being loaded (a demo visitor authenticates via
  // an already-present same-origin cookie, so it needs no wait).
  const authReady = isDemo || isLoaded;
  const [accounts, setAccounts] = useState<OAuthAccount[]>([]);
  const [connectionState, setConnectionState] = useState<"loading" | "loaded" | "error">("loading");
  const [connectionError, setConnectionError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busyProvider, setBusyProvider] = useState<string | null>(null);

  const [company, setCompany] = useState<CompanyProfile>(emptyCompany);
  const [companyState, setCompanyState] = useState<"loading" | "loaded" | "error">("loading");
  const [companyError, setCompanyError] = useState("");
  const [savingCompany, setSavingCompany] = useState(false);
  const [companySaved, setCompanySaved] = useState(false);

  const [billing, setBilling] = useState<Billing | null>(null);
  const [checkoutBusy, setCheckoutBusy] = useState<string | null>(null);
  const [checkoutError, setCheckoutError] = useState("");
  const [checkoutNotice, setCheckoutNotice] = useState<"success" | "cancel" | null>(null);

  function startCheckout(plan: string) {
    setCheckoutBusy(plan);
    setCheckoutError("");
    void api.checkout(plan).then((result) => {
      if (!result.ok) {
        setCheckoutBusy(null);
        setCheckoutError(result.error);
        return;
      }
      // Hand off to Lemon Squeezy's hosted Checkout (leaves the app).
      window.location.href = result.data.url;
    });
  }

  function openPortal() {
    setCheckoutBusy("portal");
    setCheckoutError("");
    void api.billingPortal().then((result) => {
      if (!result.ok) {
        setCheckoutBusy(null);
        setCheckoutError(result.error);
        return;
      }
      window.location.href = result.data.url;
    });
  }

  function saveCompany() {
    setSavingCompany(true);
    setCompanyError("");
    setCompanySaved(false);
    void api.saveCompany(company).then((result) => {
      setSavingCompany(false);
      if (!result.ok) {
        setCompanyError(result.error);
        return;
      }
      setCompanySaved(true);
      setTimeout(() => setCompanySaved(false), 2500);
    });
  }

  const connectedProviders = new Set(
    accounts
      .filter((account) => account.status === "connected" && !account.expired)
      .map((account) => account.provider.toLowerCase()),
  );

  function loadConnections() {
    setConnectionState("loading");
    setConnectionError("");
    void api.connections().then((result) => {
      if (!result.ok) {
        setAccounts([]);
        setConnectionError(result.error);
        setConnectionState("error");
        return;
      }
      setAccounts(result.data.accounts);
      setConnectionState("loaded");
    });
  }

  function startOAuth(provider: "gmail" | "outlook", reconnect = false) {
    setBusyProvider(provider);
    setActionError("");
    const next = reconnect ? api.oauthReconnect(provider) : api.oauthLogin(provider);
    void next.then((result) => {
      setBusyProvider(null);
      if (!result.ok) {
        setActionError(result.error);
        return;
      }
      window.location.href = result.data.url;
    });
  }

  function disconnect(provider: "gmail" | "outlook", accountEmail: string) {
    setBusyProvider(provider);
    setActionError("");
    void api.oauthDisconnect(provider, accountEmail).then((result) => {
      setBusyProvider(null);
      if (!result.ok) {
        setActionError(result.error);
        return;
      }
      loadConnections();
    });
  }

  useEffect(() => {
    if (!authReady) return;
    let mounted = true;
    setConnectionState("loading");
    void api.connections().then((result) => {
      if (!mounted) return;
      if (!result.ok) {
        setAccounts([]);
        setConnectionError(result.error);
        setConnectionState("error");
        return;
      }
      setAccounts(result.data.accounts);
      setConnectionState("loaded");
    });
    return () => {
      mounted = false;
    };
  }, [authReady]);

  useEffect(() => {
    if (!authReady) return;
    let mounted = true;
    setCompanyState("loading");
    void api.company().then((result) => {
      if (!mounted) return;
      if (!result.ok) {
        setCompanyError(result.error);
        setCompanyState("error");
        return;
      }
      setCompany({ ...emptyCompany, ...result.data.company });
      setCompanyState("loaded");
    });
    void api.billing().then((result) => {
      if (mounted && result.ok) setBilling(result.data);
    });
    return () => {
      mounted = false;
    };
  }, [authReady]);

  // Returning from Lemon Squeezy Checkout: show a confirmation/cancel note and
  // strip the ?checkout= flag so a refresh doesn't re-show the banner. The plan
  // refetch is handled by the poll effect below (it needs Clerk loaded first).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("checkout");
    if (outcome !== "success" && outcome !== "cancel") return;
    setCheckoutNotice(outcome);
    params.delete("checkout");
    const qs = params.toString();
    window.history.replaceState({}, "", window.location.pathname + (qs ? `?${qs}` : ""));
  }, []);

  // After a successful checkout the webhook flips the plan a beat later, so a
  // single fetch can race it. Once Clerk is loaded, poll briefly until the paid
  // subscription shows up (or we give up), so the card reflects the new plan
  // without a manual refresh.
  useEffect(() => {
    if (checkoutNotice !== "success" || !authReady) return;
    let cancelled = false;
    let attempts = 0;
    const tick = () => {
      void api.billing().then((result) => {
        if (cancelled) return;
        if (result.ok) {
          setBilling(result.data);
          // Stop as soon as an active/paid subscription is attributed.
          if (result.data.status && result.data.status !== "none") return;
        }
        attempts += 1;
        if (attempts < 6) setTimeout(tick, 1500);
      });
    };
    tick();
    return () => {
      cancelled = true;
    };
  }, [checkoutNotice, authReady]);

  return (
    <div>
      <PageHeader title="Settings / Connections" subtitle="Connect mailboxes and manage workspace basics." />
      <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Company details</CardTitle>
              <Building2 className="size-4 text-muted" />
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              {companyState === "loading" ? (
                <div className="md:col-span-2 grid gap-4 md:grid-cols-2">
                  <Skeleton className="h-16" />
                  <Skeleton className="h-16" />
                  <Skeleton className="h-16" />
                  <Skeleton className="h-16" />
                </div>
              ) : (
                <>
                  <p className="md:col-span-2 -mt-1 text-xs text-muted">
                    Saqua remembers these in every chat and researches, qualifies, and drafts
                    outreach on your company&apos;s behalf.
                  </p>
                  {companyFields.map((f) => (
                    <Field
                      key={f.key}
                      label={f.label}
                      className={f.key === "one_liner" || f.key === "value_prop" ? "md:col-span-2" : undefined}
                    >
                      <Input
                        value={company[f.key]}
                        placeholder={f.placeholder}
                        onChange={(e) => setCompany((c) => ({ ...c, [f.key]: e.target.value }))}
                      />
                    </Field>
                  ))}
                  {companyError && (
                    <div className="md:col-span-2 rounded-md border border-danger-soft bg-danger-soft p-3 text-sm text-danger">
                      {companyError}
                    </div>
                  )}
                  <div className="md:col-span-2 flex items-center justify-end gap-3 border-t border-border-faint pt-4">
                    {companySaved && <span className="text-xs text-success">Saved.</span>}
                    <Button variant="primary" onClick={saveCompany} disabled={savingCompany}>
                      <Save className="size-4" /> {savingCompany ? "Saving..." : "Save company"}
                    </Button>
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Mailbox connections</CardTitle>
              <Mail className="size-4 text-muted" />
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-2">
              {connectionState === "loading" && (
                <div className="md:col-span-2 grid gap-3 md:grid-cols-2">
                  <Skeleton className="h-40" />
                  <Skeleton className="h-40" />
                </div>
              )}
              {connectionState === "error" && (
                <div className="md:col-span-2">
                  <EmptyState
                    icon={Mail}
                    title="Mailbox status unavailable."
                    body={`Saqua could not reach the connections endpoint: ${connectionError || "unknown error"}`}
                    action={<Button variant="primary" onClick={loadConnections}>Retry mailbox check</Button>}
                  />
                </div>
              )}
              {actionError && connectionState === "loaded" && (
                <div className="md:col-span-2 rounded-md border border-danger-soft bg-danger-soft p-3 text-sm text-danger">
                  {actionError}
                </div>
              )}
              {connectionState === "loaded" && connectedProviders.size === 0 && (
                <div className="md:col-span-2">
                  <EmptyState
                    icon={Mail}
                    title={isDemo ? "Mailbox connections: coming soon" : "No mailbox connected."}
                    body={
                      isDemo
                        ? "Connecting Gmail and sending is in final review with Google. Everything up to the send is live in this demo; real sending opens the moment it clears."
                        : "Connect Gmail or Outlook before launching campaigns so Saqua can send and stop on replies."
                    }
                    action={
                      isDemo ? undefined : (
                        <Button variant="primary" onClick={() => startOAuth("gmail")} disabled={busyProvider === "gmail"}>
                          Connect Gmail
                        </Button>
                      )
                    }
                  />
                </div>
              )}
              {connectionState === "loaded" && mailboxProviders.map(([provider, name, desc]) => {
                const connected = connectedProviders.has(provider);
                const account = accounts.find(
                  (item) => item.provider.toLowerCase() === provider && item.status === "connected" && !item.expired,
                );
                return (
                  <div key={name} className="rounded-lg border border-border bg-black/[0.02] p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="grid size-10 place-items-center rounded-md bg-accent-soft text-accent">
                        <Plug className="size-5" />
                      </div>
                      <Badge tone={isDemo ? "neutral" : connected ? "success" : "neutral"} dot>
                        {isDemo ? "Coming soon" : connected ? "Connected" : "Available"}
                      </Badge>
                    </div>
                    <div className="mt-4 font-medium text-text">{name}</div>
                    <div className="mt-1 text-xs text-muted">{desc}</div>
                    {account && !isDemo && (
                      <div className="mt-3 truncate text-xs text-text-2">{account.account_email}</div>
                    )}
                    <div className="mt-4 flex gap-2">
                      {isDemo ? (
                        <Button variant="secondary" size="sm" className="flex-1" disabled
                                title="In final review with Google, coming soon">
                          Coming soon
                        </Button>
                      ) : (
                        <>
                          <Button
                            variant={connected ? "ghost" : "secondary"}
                            size="sm"
                            className="flex-1"
                            onClick={() => startOAuth(provider, connected)}
                            disabled={busyProvider === provider}
                          >
                            {connected ? "Reconnect" : "Connect"}
                          </Button>
                          {account && (
                            <Button
                              variant="danger"
                              size="sm"
                              onClick={() => disconnect(provider, account.account_email)}
                              disabled={busyProvider === provider}
                            >
                              Disconnect
                            </Button>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Profile</CardTitle>
              <Users className="size-4 text-muted" />
            </CardHeader>
            {isDemo ? (
              // A demo visitor has no account, so the place a real profile would
              // sit says exactly that — honest and simple — and points to the
              // waitlist rather than showing a fake identity.
              <CardContent>
                <p className="text-sm leading-6 text-text-2">
                  You&apos;re in a live demo. No account is created and nothing here is saved.
                  This sandbox resets when your session ends.
                </p>
                <Button asChild variant="primary" className="mt-4">
                  <Link href="/#waitlist">Join the waitlist to set up your own workspace</Link>
                </Button>
              </CardContent>
            ) : (
              // The signed-in identity, read from the account itself. These were
              // hardcoded to one person's name, email and timezone, so every
              // user saw somebody else's details presented as their own, above a
              // permanently disabled Save button. They are read-only because
              // sign-in owns them: editable-looking fields that cannot be saved
              // are worse than fields that never claimed to be editable.
              <CardContent className="grid gap-4 md:grid-cols-2">
                <Field label="Name">
                  <Input value={user?.fullName || "—"} readOnly disabled />
                </Field>
                <Field label="Email">
                  <Input
                    value={user?.primaryEmailAddress?.emailAddress || "—"}
                    readOnly
                    disabled
                  />
                </Field>
                <div className="md:col-span-2 border-t border-border-faint pt-4">
                  <p className="text-xs leading-5 text-muted">
                    Your name and email come from the account you signed in with. Use the
                    account menu in the sidebar to change them.
                  </p>
                </div>
              </CardContent>
            )}
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Your plan</CardTitle>
              <CreditCard className="size-4 text-muted" />
            </CardHeader>
            <CardContent>
              {checkoutNotice === "success" && (
                <div className="mb-4 rounded-md border border-success-soft bg-success-soft p-3 text-sm text-success">
                  Payment received. Your plan is updated — it can take a few seconds to
                  reflect here after a fresh checkout.
                </div>
              )}
              {checkoutNotice === "cancel" && (
                <div className="mb-4 rounded-md border border-border-faint bg-black/[0.02] p-3 text-sm text-text-2">
                  Checkout canceled. You&apos;re still on your current plan.
                </div>
              )}

              <div className="rounded-lg border border-accent-line bg-accent-soft p-5">
                <div className="text-sm text-muted">Current plan</div>
                <div className="mt-2 text-2xl font-semibold capitalize text-text">
                  {billing ? `${billing.plan} plan` : "…"}
                </div>
                {billing && billing.status === "past_due" && (
                  <div className="mt-2 rounded-md border border-danger-soft bg-danger-soft p-2 text-xs text-danger">
                    Your last payment failed. Update your card to keep your plan.
                  </div>
                )}
                {billing && billing.prospect_limit > 0 ? (
                  <>
                    <div className="mt-1 text-sm text-text-2">
                      {billing.prospects_used} of {billing.prospect_limit} prospects used
                    </div>
                    <div className="mt-4 h-1.5 w-full overflow-hidden rounded-full bg-black/[0.08]">
                      <div
                        className="h-full rounded-full bg-accent transition-all"
                        style={{
                          width: `${Math.min(100, Math.round((billing.prospects_used / billing.prospect_limit) * 100))}%`,
                        }}
                      />
                    </div>
                    <div className="mt-3 text-xs text-muted">
                      {(billing.prospects_remaining ?? 0) > 0
                        ? `${billing.prospects_remaining} prospect${billing.prospects_remaining === 1 ? "" : "s"} left on the ${billing.plan} plan.`
                        : `You've used all ${billing.prospect_limit} prospects on the ${billing.plan} plan. Upgrade to keep going.`}
                    </div>
                  </>
                ) : (
                  <div className="mt-1 text-sm text-text-2">
                    {billing ? "Unlimited prospects" : "Loading your plan…"}
                  </div>
                )}
              </div>

              {checkoutError && (
                <div className="mt-4 rounded-md border border-danger-soft bg-danger-soft p-3 text-sm text-danger">
                  {checkoutError}
                </div>
              )}

              {isDemo ? (
                // A demo visitor has no account to attach a subscription to, so the
                // upgrade path points them to create one rather than 401ing on checkout.
                <Button asChild variant="primary" className="mt-5 w-full">
                  <Link href="/#waitlist">Create an account to upgrade</Link>
                </Button>
              ) : (
                <div className="mt-5 space-y-2">
                  {planTiers
                    .filter((tier) => !billing || tier.prospects > billing.prospect_limit)
                    .map((tier) => (
                      <Button
                        key={tier.id}
                        variant="primary"
                        className="w-full justify-between"
                        onClick={() => startCheckout(tier.id)}
                        disabled={checkoutBusy !== null}
                      >
                        <span>
                          {checkoutBusy === tier.id ? "Starting checkout…" : `Upgrade to ${tier.name}`}
                        </span>
                        <span className="text-xs opacity-80">
                          {tier.prospects} prospects · ${tier.price}/mo
                        </span>
                      </Button>
                    ))}

                  {billing && billing.status && !["none", ""].includes(billing.status) && (
                    <Button
                      variant="secondary"
                      className="w-full"
                      onClick={openPortal}
                      disabled={checkoutBusy !== null}
                    >
                      {checkoutBusy === "portal" ? "Opening…" : "Manage billing"}
                    </Button>
                  )}

                  <Button asChild variant="ghost" className="w-full">
                    <Link href="/contact">Need more? Talk to us about Enterprise</Link>
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Workspace settings</CardTitle>
              <Settings2 className="size-4 text-muted" />
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-text-2">
              <div className="flex justify-between rounded-md border border-border-faint bg-black/[0.02] p-3">
                <span>Stop sequence on reply</span>
                <Badge tone="success">On</Badge>
              </div>
              <div className="flex justify-between rounded-md border border-border-faint bg-black/[0.02] p-3">
                <span>Guard review required</span>
                <Badge tone="success">On</Badge>
              </div>
              <div className="flex justify-between rounded-md border border-border-faint bg-black/[0.02] p-3">
                <span>Daily send cap</span>
                <span className="text-text">75</span>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
