import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

const RAW_API_ORIGIN = process.env.SAQUA_API_ORIGIN?.trim();
const IS_PRODUCTION = process.env.NODE_ENV === "production";
// Local-dev convenience only. In production the origin MUST come from
// SAQUA_API_ORIGIN — if it is unset, requests fail loudly (see `proxy`) instead of
// silently hitting a nonexistent localhost backend.
const DEV_FALLBACK_ORIGIN = "http://127.0.0.1:8000";
const API_ORIGIN = RAW_API_ORIGIN
  ? RAW_API_ORIGIN.replace(/\/+$/, "")
  : IS_PRODUCTION
    ? ""
    : DEV_FALLBACK_ORIGIN;

if (IS_PRODUCTION && !API_ORIGIN) {
  console.error(
    JSON.stringify({
      event: "api_proxy_misconfigured",
      reason: "SAQUA_API_ORIGIN is not set in production — /api requests will 500",
    }),
  );
} else if (!RAW_API_ORIGIN && !IS_PRODUCTION) {
  console.warn(
    JSON.stringify({
      event: "api_proxy_dev_fallback",
      origin: DEV_FALLBACK_ORIGIN,
      hint: "Set SAQUA_API_ORIGIN in saqua-frontend/.env.local to point at your backend",
    }),
  );
}

const PROXY_TIMEOUT_MS = 10 * 60 * 1000;
const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
]);

function backendUrl(path: string[], search: string): string {
  const origin = API_ORIGIN.replace(/\/+$/, "");
  const suffix = path.map(encodeURIComponent).join("/");
  return `${origin}/api/${suffix}${search}`;
}

function logSafeUrl(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.search) {
      parsed.search = "?<redacted>";
    }
    return parsed.toString();
  } catch {
    return url.split("?")[0] + (url.includes("?") ? "?<redacted>" : "");
  }
}

function logProxy(level: "info" | "error", event: string, data: Record<string, unknown>) {
  const payload = JSON.stringify({ event, ...data });
  if (level === "error") {
    console.error(payload);
  } else {
    console.info(payload);
  }
}

/**
 * The caller's real address as seen by our own edge.
 *
 * A proxy APPENDS the peer it saw, so the RIGHTMOST entry of the incoming
 * X-Forwarded-For is the one our platform added — the only entry a client cannot
 * forge. Everything to its left is attacker-supplied. Returns "" when there is no
 * trustworthy value (e.g. local dev with no edge in front).
 */
function clientIp(request: NextRequest): string {
  const xff = request.headers.get("x-forwarded-for") || "";
  const parts = xff.split(",").map((p) => p.trim()).filter(Boolean);
  if (parts.length) {
    return parts[parts.length - 1];
  }
  return (request.headers.get("x-real-ip") || "").trim();
}

function forwardedHeaders(request: NextRequest, traceId: string): Headers {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    const normalized = key.toLowerCase();
    if (!HOP_BY_HOP_HEADERS.has(normalized)) {
      headers.set(key, value);
    }
  });
  // Collapse X-Forwarded-For to the single trustworthy value before it reaches
  // the backend. Without this the backend sees THIS proxy as the client (so every
  // visitor shares one rate-limit bucket) and any header the browser invented
  // passes straight through (so the limit is trivially bypassed). When no
  // trustworthy value exists we DELETE the header rather than forward a forged
  // one — the backend then falls back to the proxy address, which is
  // over-restrictive but never permissive.
  const ip = clientIp(request);
  if (ip) {
    headers.set("x-forwarded-for", ip);
  } else {
    headers.delete("x-forwarded-for");
  }
  headers.set("x-forwarded-host", request.headers.get("host") || "");
  headers.set("x-forwarded-proto", request.nextUrl.protocol.replace(":", ""));
  headers.set("x-request-id", traceId);
  headers.set("x-saqua-proxy-trace-id", traceId);
  return headers;
}

async function proxy(request: NextRequest, { params }: { params: { path: string[] } }) {
  const started = Date.now();
  const traceId = randomUUID();

  if (!API_ORIGIN) {
    logProxy("error", "api_proxy_misconfigured", {
      trace_id: traceId,
      reason: "SAQUA_API_ORIGIN is not configured",
    });
    return NextResponse.json(
      {
        error: "Backend not configured",
        reason: "SAQUA_API_ORIGIN is not set for this environment.",
        trace_id: traceId,
      },
      { status: 500, headers: { "x-saqua-trace-id": traceId } },
    );
  }
  const path = `/api/${(params.path || []).join("/")}`;
  const url = backendUrl(params.path || [], request.nextUrl.search);
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new Error("Backend request timed out")), PROXY_TIMEOUT_MS);

  logProxy("info", "api_proxy_start", {
    trace_id: traceId,
    method: request.method,
    path,
    target_url: logSafeUrl(url),
    request_body_bytes: body?.byteLength || 0,
  });

  try {
    const upstream = await fetch(url, {
      method: request.method,
      headers: forwardedHeaders(request, traceId),
      body,
      cache: "no-store",
      signal: controller.signal,
    });

    const responseBytes = await upstream.arrayBuffer();
    const durationMs = Date.now() - started;
    const contentType = upstream.headers.get("content-type") || "application/octet-stream";
    const responseHeaders = new Headers();
    upstream.headers.forEach((value, key) => {
      if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
        responseHeaders.set(key, value);
      }
    });
    responseHeaders.set("content-type", contentType);
    responseHeaders.set("x-saqua-trace-id", traceId);

    logProxy("info", "api_proxy_complete", {
      trace_id: traceId,
      method: request.method,
      path,
      target_url: logSafeUrl(url),
      backend_status: upstream.status,
      backend_content_type: contentType,
      backend_response_bytes: responseBytes.byteLength,
      duration_ms: durationMs,
    });

    return new NextResponse(responseBytes, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    const durationMs = Date.now() - started;
    const reason = error instanceof Error ? error.message : "Unknown proxy error";
    logProxy("error", "api_proxy_failed", {
      trace_id: traceId,
      method: request.method,
      path,
      target_url: logSafeUrl(url),
      request_body_bytes: body?.byteLength || 0,
      duration_ms: durationMs,
      reason,
      stack: error instanceof Error ? error.stack : undefined,
    });
    return NextResponse.json(
      {
        error: "Campaign service proxy failed",
        reason,
        trace_id: traceId,
      },
      {
        status: 502,
        headers: { "x-saqua-trace-id": traceId },
      },
    );
  } finally {
    clearTimeout(timeout);
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
