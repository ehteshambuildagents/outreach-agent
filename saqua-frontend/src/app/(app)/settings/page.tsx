"use client";

import { useEffect, useState } from "react";
import { CreditCard, Mail, Plug, Save, Settings2, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, Input } from "@/components/ui/field";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type OAuthAccount } from "@/lib/api";

const mailboxProviders = [
  ["gmail", "Gmail", "Send, receive, and stop sequences on reply"],
  ["outlook", "Outlook", "Connect Microsoft inboxes for sending"],
] as const;

export default function SettingsPage() {
  const [accounts, setAccounts] = useState<OAuthAccount[]>([]);
  const [connectionState, setConnectionState] = useState<"loading" | "loaded" | "error">("loading");
  const [connectionError, setConnectionError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busyProvider, setBusyProvider] = useState<string | null>(null);

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
  }, []);

  return (
    <div>
      <PageHeader title="Settings / Connections" subtitle="Connect mailboxes and manage workspace basics." />
      <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <div className="space-y-4">
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
                    title="No mailbox connected."
                    body="Connect Gmail or Outlook before launching campaigns so Saqua can send and stop on replies."
                    action={
                      <Button variant="primary" onClick={() => startOAuth("gmail")} disabled={busyProvider === "gmail"}>
                        Connect Gmail
                      </Button>
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
                  <div key={name} className="rounded-lg border border-border bg-white/[0.02] p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="grid size-10 place-items-center rounded-md bg-accent-soft text-accent-hi">
                        <Plug className="size-5" />
                      </div>
                      <Badge tone={connected ? "success" : "neutral"} dot>
                        {connected ? "Connected" : "Available"}
                      </Badge>
                    </div>
                    <div className="mt-4 font-medium text-text">{name}</div>
                    <div className="mt-1 text-xs text-muted">{desc}</div>
                    {account && <div className="mt-3 truncate text-xs text-text-2">{account.account_email}</div>}
                    <div className="mt-4 flex gap-2">
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
            <CardContent className="grid gap-4 md:grid-cols-2">
              <Field label="Name">
                <Input defaultValue="Ehtesham Munir" />
              </Field>
              <Field label="Email">
                <Input defaultValue="ehtesham@saqua.ai" />
              </Field>
              <Field label="Workspace">
                <Input defaultValue="Saqua" />
              </Field>
              <Field label="Timezone">
                <Input defaultValue="UTC+05 Islamabad, Karachi" />
              </Field>
              <div className="md:col-span-2 flex justify-end border-t border-border-faint pt-4">
                <Button variant="primary" disabled title="Backend endpoint missing for profile updates">
                  <Save className="size-4" /> Save changes
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Billing placeholder</CardTitle>
              <CreditCard className="size-4 text-muted" />
            </CardHeader>
            <CardContent>
              <div className="rounded-lg border border-accent-line bg-accent-soft p-5">
                <div className="text-sm text-muted">Current plan</div>
                <div className="mt-2 text-2xl font-semibold text-text">Pro Plan</div>
                <div className="mt-1 text-sm text-text-2">$49 / month</div>
                <div className="mt-5 space-y-2 text-xs text-muted">
                  <div>2,500 emails/month</div>
                  <div>Unlimited campaigns</div>
                  <div>Priority support</div>
                </div>
              </div>
              <Button variant="secondary" className="mt-5 w-full" disabled title="Billing backend is not connected yet">
                Manage billing
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Workspace settings</CardTitle>
              <Settings2 className="size-4 text-muted" />
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-text-2">
              <div className="flex justify-between rounded-md border border-border-faint bg-white/[0.02] p-3">
                <span>Stop sequence on reply</span>
                <Badge tone="success">On</Badge>
              </div>
              <div className="flex justify-between rounded-md border border-border-faint bg-white/[0.02] p-3">
                <span>Guard review required</span>
                <Badge tone="success">On</Badge>
              </div>
              <div className="flex justify-between rounded-md border border-border-faint bg-white/[0.02] p-3">
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
