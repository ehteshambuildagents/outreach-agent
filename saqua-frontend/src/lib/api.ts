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

export interface ManualRecipientRequest {
  name: string;
  email: string;
}

// ── Chat workspace (research_prospects / draft_channel_message cards) ───
export type ChatMessageKind = "text" | "email" | "research" | "notice" | "prospects" | "channel";

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
    score_breakdown?: Record<string, unknown>;
    strongest_signals?: string[];
    missing_information?: string[];
    disqualifiers?: string[];
    why_discovered?: string | null;
  };
}

export interface ProspectsCardData {
  summary?: { total?: number; researched?: number; top?: string | null; by_recommendation?: Record<string, number> };
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

export const api = {
  publicConfig: () => req<PublicConfig>("/api/public-config"),
  health: () => req<{ ok: boolean }>("/api/health"),

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
  prospects: () => req<{ prospects: CampaignPreviewProspect[] }>("/api/prospects"),

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
};
