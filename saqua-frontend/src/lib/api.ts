/**
 * Thin client over the existing Saqua FastAPI backend (proxied via next.config
 * rewrites to /api/*). Every call is defensive: it never throws into the UI —
 * on failure it returns a typed `{ ok: false }` so pages can render a fallback.
 *
 * The endpoint surface mirrors the real backend:
 *   /api/automation/*        workflows, metrics, dead-letter, health
 *   /api/oauth/accounts      connected mailboxes (connections page)
 *   /api/conversations/*     chat workspace (writer/research live)
 * Where a page needs data the backend does not yet expose, it degrades to
 * clearly-labelled placeholder state rather than inventing an endpoint.
 */

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: string; status?: number };

declare global {
  interface Window {
    Clerk?: {
      session?: {
        getToken: () => Promise<string | null>;
      };
    };
  }
}

type TokenProvider = () => Promise<string | null>;

let tokenProvider: TokenProvider | null = null;

export function setApiTokenProvider(provider: TokenProvider | null) {
  tokenProvider = provider;
}

async function authToken(): Promise<string | null> {
  if (tokenProvider) {
    return tokenProvider();
  }
  if (typeof window !== "undefined") {
    const clerk = window.Clerk;
    const session = clerk?.session;
    if (session?.getToken) {
      return session.getToken();
    }
  }
  return null;
}

async function req<T>(path: string, init?: RequestInit): Promise<ApiResult<T>> {
  try {
    const headers = new Headers(init?.headers);
    if (!headers.has("Content-Type") && init?.body) {
      headers.set("Content-Type", "application/json");
    }
    const token = await authToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }

    const res = await fetch(path, {
      ...init,
      headers,
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!res.ok) {
      if (res.status === 401) {
        return { ok: false, error: "Please sign in to continue.", status: 401 };
      }
      let message = `HTTP ${res.status}`;
      try {
        const payload = (await res.json()) as { error?: string; detail?: string; reason?: string; trace_id?: string };
        message = payload.error || payload.detail || message;
        if (payload.reason) {
          message = `${message}: ${payload.reason}`;
        }
        if (payload.trace_id) {
          message = `${message} (trace ${payload.trace_id})`;
        }
      } catch {
        // Keep the generic HTTP status when the backend returns a non-JSON error.
      }
      return { ok: false, error: message, status: res.status };
    }
    const data = (await res.json()) as T;
    return { ok: true, data };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "network error" };
  }
}

// ── Automation / campaigns ────────────────────────────────────────────
export interface WorkflowStatus {
  id: string;
  state: string;
  company: string;
  provider: string;
  to: string;
  current_step: number;
  total_steps: number;
  next_run_at: number | null;
  reply_detected: boolean;
  retry_count: number;
  last_error: string | null;
  last_execution: number;
}

export interface WorkflowEvent {
  ts?: number;
  at?: number;
  type?: string;
  event?: string;
  state?: string;
  message?: string;
  detail?: string;
}

export interface CreateWorkflowRequest {
  to_email: string;
  company?: string;
  provider?: "dryrun" | "gmail" | "outlook";
  conversation_id?: string;
  timezone?: string;
  steps: {
    subject: string;
    body: string;
    to?: string;
    artifact_id?: string;
    delay_days?: number;
  }[];
}

export interface AutomationMetrics {
  metrics: Record<string, number>;
  by_state: Record<string, number>;
}

export interface OAuthAccount {
  provider: string;
  account_email: string;
  status: string;
  expires_at: number | null;
  expired: boolean;
}

export interface PublicConfig {
  clerkPublishableKey: string | null;
  authEnabled: boolean;
}

/** The sender's own company details, remembered in every chat (GET/PUT /api/company). */
export interface CompanyProfile {
  name: string;
  website: string;
  one_liner: string;
  audience: string;
  value_prop: string;
  tone: string;
}

/** One purchasable plan in the canonical catalog (billing.plan_catalog). */
export interface PlanCatalogEntry {
  plan: string;
  name: string;
  price: number;
  currency: string;
  interval: "monthly" | "yearly";
  prospect_limit: number;
}

/** The user's real plan + usage (GET /api/billing). */
export interface Billing {
  plan: string;
  /** Human name for the current plan ("Free" | "Pro" | "Max" | "Enterprise"). */
  plan_name?: string;
  prospect_limit: number;
  prospects_used: number;
  prospects_remaining: number | null;
  /** LS subscription status ("active" | "on_trial" | "past_due" | "cancelled" | ... | "none"). */
  status?: string;
  /** Unix seconds the current paid period ends, when on a paid plan. */
  current_period_end?: number | null;
  /** True on any paid tier — upgrade surfaces hide for these users. */
  is_paid?: boolean;
  /** Canonical purchasable plans (name/price/limit) — the ONLY source the upgrade
   *  UI reads, so no price is ever hardcoded in a component. */
  catalog?: PlanCatalogEntry[];
  /** The next paid tier to pitch ("pro" | "max"), or null when nothing is left. */
  recommended_upgrade?: string | null;
  /** Whether a real Lemon Squeezy checkout can be started (false for demo visitors,
   *  who have no account and must be routed to /pricing instead). */
  checkout_enabled?: boolean;
}

export interface CampaignSummary {
  discovered: number;
  research_ok: number;
  qualified: number;
  emails_generated: number;
  guard_allowed: number;
  guard_blocked: number;
  launchable: number;
  route_only: number;
  estimated_cost: number;
  latency_seconds: number;
}

export interface CampaignPreviewProspect {
  company_name: string;
  website: string;
  domain: string;
  confidence: number;
  final_status: string;
  reason: string | null;
  research?: {
    status: string;
    reason?: string;
    company_name?: string;
    summary?: string;
    pages_crawled: string[];
    research_score?: number;
    named_person?: string;
    recipient?: {
      name?: string | null;
      role?: string | null;
      email?: string | null;
      linkedin?: string | null;
      contact_page?: string | null;
      route?: string | null;
    };
    hooks: string[];
  };
  qualification?: {
    recommendation: string;
    qualification_score: number;
    confidence?: number;
  };
  strategy?: {
    recommended_action: string;
    confidence: number;
    primary_hook?: string | null;
  };
  email?: {
    status: string;
    reason?: string | null;
    subject?: string | null;
    body?: string | null;
    to?: string | null;
    recipient?: {
      name?: string | null;
      role?: string | null;
      email?: string | null;
      linkedin?: string | null;
      contact_page?: string | null;
      route?: string | null;
    };
  };
  guard?: {
    decision: "ALLOW" | "WARN" | "BLOCK";
    overallRisk: number;
  };
  manual_recipient?: {
    name?: string | null;
    email?: string | null;
  };
  followups?: unknown[];
  /** Present when this prospect would launch with only a 3-step cadence (no
   * mid-cadence follow-ups). `code` distinguishes the cause. */
  cadence_warning?: {
    code: "manual_recipient_no_followups" | "followups_dropped_at_create";
    planned_steps: number;
    message: string;
  };
}

export interface CampaignDetail {
  id: string;
  name: string;
  status: string;
  created_at: number;
  updated_at: number;
  summary: CampaignSummary;
  workflow_ids: string[];
  request: unknown;
  result: {
    status: string;
    reason?: string;
    summary: CampaignSummary;
    prospects: CampaignPreviewProspect[];
    launched_with_warnings?: { domain: string; code: string; planned_steps: number; message: string }[];
  };
  events: WorkflowEvent[];
  workflows: WorkflowStatus[];
  idempotent: boolean;
}

export interface CampaignCreateRequest {
  name: string;
  limit: number;
  idempotency_key?: string;
  icp: {
    raw: string;
    industry?: string;
    location?: string;
    employee_range?: string;
    funding_stage?: string;
    keywords?: string[];
    exclude_keywords?: string[];
  };
}

/** A discovered ICP-matching company from GET /api/prospects (discovery store,
 * `Prospect.public()`). Company-level and pre-research — no person, no fit score. */
export interface DiscoveryProspect {
  company_name: string;
  website?: string | null;
  industry?: string | null;
  location?: string | null;
  estimated_company_size?: string | null;
  estimated_stage?: string | null;
  confidence?: number | null;
  why_it_matches?: string | null;
  discovery_source?: string | null;
  basic_signals?: string[] | null;
}

export interface ManualRecipientRequest {
  name: string;
  email: string;
}

// ── Chat workspace (research_prospects / draft_channel_message cards) ───
export type ChatMessageKind =
  | "text"
  | "email"
  | "research"
  | "notice"
  | "prospects"
  | "channel"
  | "stats"
  | "replies"
  | "campaigns";

export interface ChatMessage {
  role: "user" | "assistant";
  kind: ChatMessageKind;
  content: string;
  data: unknown | null;
}

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  panel?: unknown;
  /** The true active research target (workspace.company), for the "Researching: X"
   *  indicator. Null when no company is on file yet. */
  active_company?: string | null;
}

/** Sidebar row from GET /api/conversations (list_summaries). */
export interface ConversationSummary {
  id: string;
  title: string;
  updated_at?: number | null;
}

/** One finding in a prospect's expanded detail — reuses the research enrichment
 * schema (value + source + confidence). */
export interface ProspectFinding {
  label: string;
  value: string;
  source?: string | null;
  confidence?: number | null;
  quote?: string | null;
}

/** A researched + scored prospect (backend `_prospect_public`). `preview` is the
 * collapsed one-paragraph headline; `detail` is revealed on expand. */
export interface ProspectEntry {
  company: string;
  website: string;
  status: string;
  score: number;
  fit_level: string;
  /** Honest qualification band shown on the card. Present on discovered entries. */
  band?: "strong" | "possible" | "weak";
  priority: string;
  recommendation: string;
  recommended: boolean;
  score_reason: string;
  preview: string;
  actions: string[];
  detail: {
    what_they_do?: string | null;
    research_confidence?: number | null;
    findings?: ProspectFinding[];
    sources?: { domain: string; url: string }[];
    /** Customer-likelihood factor breakdown (0..1 each), used to render the
     *  plain-language "Why this ranked here" checklist on a discovered card. */
    score_breakdown?: {
      total?: number;
      icp_match?: number;
      buying_signal?: number;
      hiring_relevance?: number;
      founder_access?: number;
      corroboration?: number;
    } | null;
    is_public?: boolean;
    /** A named contact to reach on a researched prospect (decision-maker
     *  availability), or null when the research surfaced no named person. */
    decision_maker?: { name: string; role?: string | null } | null;
    /** USD, from Apollo. Used to caveat very large PRIVATE companies, which are
     *  demoted as buyers just like listed ones. */
    annual_revenue?: number | null;
    industry_kind?: string;
    strongest_signals?: string[];
    missing_information?: string[];
    disqualifiers?: string[];
    why_discovered?: string | null;
    /** Present on DISCOVERED entries (status "discovered"), which are leads that
     *  have not been researched yet: the itemised reasons they were selected, the
     *  structurally verified hiring signal, and headcount growth. */
    match_reasons?: string[];
    hiring?: {
      verified?: boolean;
      source?: string;
      /** "role" = a live posting matching the role asked for; "any" = open roles
       *  but none matching; "page" = only a careers page. */
      match?: "role" | "any" | "page";
      summary?: string;
      url?: string;
      postings?: { title?: string; url?: string; posted_at?: string }[];
    } | null;
    recent_activity?: { summary?: string; url?: string; source?: string } | null;
    growth?: { headcount_6mo?: number | null; headcount_12mo?: number | null } | null;
    kind?: string;
    tier?: string;
  };
}

export interface ProspectsCardData {
  summary?: {
    total?: number;
    researched?: number;
    top?: string | null;
    by_recommendation?: Record<string, number>;
    /** Discovery runs only: how many candidates were weighed and how many
     *  job boards / aggregators were demoted out of the results. */
    discovered?: number;
    considered?: number;
    demoted?: number;
  };
  prospects: ProspectEntry[];
}

/** A safe-channel draft (backend `channel` card). `posted` is always false — this
 * feature drafts only; the user posts manually. */
export interface ChannelCardData {
  channel: string;
  label: string;
  body: string;
  char_count?: number;
  company?: string | null;
  posted: boolean;
  guard?: "ALLOW" | "WARN" | "BLOCK" | null;
}

export interface EmailCardData {
  subject?: string | null;
  body?: string | null;
  to?: string | null;
  company?: string | null;
  label?: string | null;
  /** Optional — only present if the backend attaches the prospect's fit score /
   * pre-send check count to the draft payload. The artifact panel shows them when
   * present and omits them otherwise (never fabricated). */
  fit_score?: number | null;
  checks_passed?: number | null;
}

export interface ResearchCardData {
  company?: string | null;
  what_they_do?: string | null;
  research_score?: number | null;
  pages_crawled?: string[];
  hooks?: { text?: string }[] | string[];
  stop_reason?: string | null;
}

// ── Co-founder cards — the user's REAL operating state (never fabricated) ──

/** Live outreach analytics for the signed-in user (backend `stats` card). */
export interface StatsCardData {
  emails_sent: number;
  replies: number;
  reply_rate: number; // fraction, e.g. 0.18
  sequences_active: number;
  sequences_paused: number;
  prospects_contacted: number;
  campaigns: number;
}

/** One prospect that replied. Reply TEXT is never stored — who + when only. */
export interface ReplyEntry {
  company: string;
  to?: string | null;
  replied_at?: number | null;
  emails_before_reply?: number | null;
}

export interface RepliesCardData {
  count: number;
  replies: ReplyEntry[];
}

/** One of the user's campaigns and where it stands (backend `campaigns` card). */
export interface CampaignEntry {
  id: string;
  name: string;
  status: string;
  launched: number; // live sequences launched from it
  discovered?: number | null;
  updated_at?: number | null;
}

export interface CampaignsCardData {
  count: number;
  campaigns: CampaignEntry[];
}

export const api = {
  publicConfig: () => req<PublicConfig>("/api/public-config"),
  health: () => req<{ ok: boolean; redis?: string; production?: boolean }>("/api/health"),

  /** Public pre-launch waitlist (no auth). `company` is the honeypot: it is
   *  rendered hidden, so a real person never fills it and a bot always does. */
  joinWaitlist: (body: { email: string; source?: string; company?: string }) =>
    req<{ ok: boolean }>("/api/waitlist", { method: "POST", body: JSON.stringify(body) }),

  /** Public contact form (no auth). Emails the support inbox with the sender as
   *  Reply-To. `company` is the honeypot, same convention as the waitlist. */
  sendContact: (body: { email: string; message: string; subject?: string; company?: string }) =>
    req<{ ok: boolean }>("/api/contact", { method: "POST", body: JSON.stringify(body) }),

  company: () => req<{ company: Partial<CompanyProfile> }>("/api/company"),
  saveCompany: (body: CompanyProfile) =>
    req<{ company: CompanyProfile }>("/api/company", { method: "PUT", body: JSON.stringify(body) }),
  billing: () => req<Billing>("/api/billing"),
  /** Start a Lemon Squeezy Checkout for a paid plan; returns the URL to redirect to. */
  checkout: (plan: string, interval: "monthly" | "yearly" = "monthly") =>
    req<{ url: string; id?: string }>("/api/billing/checkout", {
      method: "POST",
      body: JSON.stringify({ plan, interval }),
    }),
  /** Open the Lemon Squeezy customer portal to manage/cancel an existing subscription. */
  billingPortal: () => req<{ url: string }>("/api/billing/portal", { method: "POST" }),

  workflows: () => req<{ workflows: WorkflowStatus[] }>("/api/automation/workflows"),
  workflow: (id: string) => req<WorkflowStatus & { events: WorkflowEvent[] }>(`/api/automation/workflows/${id}`),
  createWorkflow: (body: CreateWorkflowRequest) =>
    req<WorkflowStatus>("/api/automation/workflows", { method: "POST", body: JSON.stringify(body) }),
  deadLetter: () => req<{ workflows: WorkflowStatus[] }>("/api/automation/dead-letter"),
  metrics: () => req<AutomationMetrics>("/api/automation/metrics"),
  automationHealth: () =>
    req<{ status: string; checks: Record<string, { status: string; detail?: string }> }>("/api/automation/health"),
  cancel: (id: string) => req<WorkflowStatus>(`/api/automation/workflows/${id}/cancel`, { method: "POST" }),
  pause: (id: string) => req<WorkflowStatus>(`/api/automation/workflows/${id}/pause`, { method: "POST" }),
  resume: (id: string) => req<WorkflowStatus>(`/api/automation/workflows/${id}/resume`, { method: "POST" }),
  run: (id: string) => req<WorkflowStatus>(`/api/automation/workflows/${id}/run`, { method: "POST" }),
  forceRetry: (id: string) => req<WorkflowStatus>(`/api/automation/workflows/${id}/force-retry`, { method: "POST" }),
  forceComplete: (id: string) => req<WorkflowStatus>(`/api/automation/workflows/${id}/force-complete`, { method: "POST" }),

  connections: () => req<{ accounts: OAuthAccount[] }>("/api/oauth/accounts"),
  oauthLogin: (provider: "gmail" | "outlook", returnTo = "/settings") =>
    req<{ url: string }>(`/api/oauth/${provider}/login?return_to=${encodeURIComponent(returnTo)}`),
  oauthReconnect: (provider: "gmail" | "outlook", returnTo = "/settings") =>
    req<{ url: string }>(`/api/oauth/${provider}/reconnect?return_to=${encodeURIComponent(returnTo)}`),
  oauthDisconnect: (provider: "gmail" | "outlook", accountEmail: string) =>
    req<{ ok: true; disconnected: string }>(
      `/api/oauth/${provider}/disconnect?account_email=${encodeURIComponent(accountEmail)}`,
      { method: "POST" },
    ),
  oauthWatch: (provider: "gmail" | "outlook", accountEmail: string) =>
    req<Record<string, unknown>>(`/api/oauth/${provider}/watch?account_email=${encodeURIComponent(accountEmail)}`, {
      method: "POST",
    }),

  campaigns: () => req<{ campaigns: CampaignDetail[] }>("/api/campaigns"),
  campaign: (id: string) => req<CampaignDetail>(`/api/campaigns/${id}`),
  createCampaign: (body: CampaignCreateRequest) =>
    req<CampaignDetail>("/api/campaigns", { method: "POST", body: JSON.stringify(body) }),
  updateCampaignRecipient: (campaignId: string, domain: string, body: ManualRecipientRequest) =>
    req<CampaignDetail>(`/api/campaigns/${encodeURIComponent(campaignId)}/prospects/${encodeURIComponent(domain)}/recipient`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  launchCampaign: (id: string, provider: "dryrun" | "gmail" | "outlook" = "dryrun") =>
    req<CampaignDetail>(`/api/campaigns/${id}/launch`, { method: "POST", body: JSON.stringify({ provider }) }),
  pauseCampaign: (id: string) => req<CampaignDetail>(`/api/campaigns/${id}/pause`, { method: "POST" }),
  resumeCampaign: (id: string) => req<CampaignDetail>(`/api/campaigns/${id}/resume`, { method: "POST" }),
  cancelCampaign: (id: string) => req<CampaignDetail>(`/api/campaigns/${id}/cancel`, { method: "POST" }),
  prospects: () => req<{ prospects: DiscoveryProspect[] }>("/api/prospects"),

  // Chat workspace — research_prospects / draft_channel_message run here.
  conversations: () => req<{ conversations: ConversationSummary[] }>("/api/conversations"),
  createConversation: () => req<Conversation>("/api/conversations", { method: "POST" }),
  conversation: (id: string) => req<Conversation>(`/api/conversations/${encodeURIComponent(id)}`),
  renameConversation: (id: string, title: string) =>
    req<Conversation>(`/api/conversations/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  deleteConversation: (id: string) =>
    req<{ ok: boolean }>(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }),
  sendMessage: (id: string, text: string) =>
    req<Conversation>(`/api/conversations/${encodeURIComponent(id)}/messages`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  sendMessageStream,
};

/** Callbacks for a streamed agent turn. Every handler is optional so a caller can
 *  subscribe to just what it renders. */
export interface StreamHandlers {
  /** A real pipeline stage: WHAT the agent is doing right now. */
  onStep?: (label: string) => void;
  /** WHY it is doing it, or what it just decided. Derived server-side from the
   *  run's actual state (discovery/narration.py), never canned copy. */
  onThought?: (label: string) => void;
  onMessage?: (message: ChatMessage) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
  onFinal?: (conversation: Conversation) => void;
}

/**
 * Run one agent turn over Server-Sent Events, invoking `handlers` as the real
 * pipeline stages, each card, and the reply arrive. The transport is a POST whose
 * body streams `text/event-stream`; the Next proxy passes it straight through.
 *
 * Returns when the stream closes. Never throws for normal failures — a transport
 * error is delivered via `onError` and returned as `{ ok:false }`.
 */
export async function sendMessageStream(
  id: string,
  text: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<{ ok: boolean; error?: string }> {
  let res: Response;
  try {
    const headers = new Headers({ "Content-Type": "application/json", Accept: "text/event-stream" });
    const token = await authToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    res = await fetch(`/api/conversations/${encodeURIComponent(id)}/messages/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({ text }),
      cache: "no-store",
      credentials: "same-origin",
      signal,
    });
  } catch (e) {
    if ((e as Error)?.name === "AbortError") return { ok: true };
    const msg = e instanceof Error ? e.message : "network error";
    handlers.onError?.(msg);
    return { ok: false, error: msg };
  }

  if (!res.ok || !res.body) {
    let msg = `HTTP ${res.status}`;
    try {
      const p = (await res.json()) as { detail?: string; error?: string };
      msg = p.detail || p.error || msg;
    } catch {
      /* non-JSON error body */
    }
    handlers.onError?.(msg);
    return { ok: false, error: msg };
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const handle = (frame: string) => {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
    }
    if (!dataLines.length) return;
    let data: unknown;
    try {
      data = JSON.parse(dataLines.join("\n"));
    } catch {
      return;
    }
    switch (event) {
      case "step":
        handlers.onStep?.((data as { label?: string }).label || "");
        break;
      case "thought":
        handlers.onThought?.((data as { label?: string }).label || "");
        break;
      case "message":
        handlers.onMessage?.((data as { message: ChatMessage }).message);
        break;
      case "error":
        handlers.onError?.((data as { message?: string }).message || "Something went wrong.");
        break;
      case "done":
        handlers.onDone?.();
        break;
      case "final":
        handlers.onFinal?.(data as Conversation);
        break;
      default:
        break; // "open" and any future events are ignored safely
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      // Frames are separated by a blank line (\n\n).
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        if (frame.trim()) handle(frame);
      }
    }
  } catch (e) {
    if ((e as Error)?.name === "AbortError") return { ok: true };
    const msg = e instanceof Error ? e.message : "stream interrupted";
    handlers.onError?.(msg);
    return { ok: false, error: msg };
  }
  return { ok: true };
}
