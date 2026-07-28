import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// MUST stay a literal. Next reads this at build time by statically analysing the
// module, and it does not resolve identifiers: assigning from a named constant
// (`export const maxDuration = MAX_DURATION_SECONDS`) builds fine but is silently
// discarded — "Unknown identifier at maxDuration ... the default config will be
// used instead" — dropping the function back to the platform default of 10-15s.
// A campaign create runs ~200s, so every one of them was being killed by the host
// long before the 290s abort below could return an explained 502.
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
      reason: "SAQUA_API_ORIGIN is not set in production, so /api requests will 500",
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

// Must fire BEFORE the platform kills the function, or the abort below is dead
// code: at 10 minutes against a 5-minute maxDuration the host always won, and a
// slow campaign returned an opaque platform timeout instead of the explained
// 502 with a trace id that this handler is built to return. Derived from
// `maxDuration` itself so the two cannot drift apart again — reading the exported
// const here is fine, it is only the export's own initializer that Next needs to
// be a literal.
const PROXY_TIMEOUT_MS = (maxDuration - 10) * 1000;
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
 * The browser's real address.
 *
 * Vercel sets x-vercel-forwarded-for from the actual connection and overwrites
 * anything the browser sent under that name — verified against production on
 * 2026-07-19 by replaying a request with a forged value, which never arrived. It
 * is therefore the one address here that a caller cannot choose.
 *
 * X-Forwarded-For is deliberately NOT consulted: this runs behind Vercel, where
 * the left of that chain is caller-supplied, and the value is discarded downstream
 * anyway (see below). x-real-ip is kept only as a local-dev fallback, where there
 * is no edge in front and nothing to forge past.
 */
function clientIp(request: NextRequest): string {
  const vercel = (request.headers.get("x-vercel-forwarded-for") || "").trim();
  if (vercel) {
    return vercel;
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
  // Hand the browser's address to the backend in a header of our own.
  //
  // Rewriting X-Forwarded-For here does NOT work and was measured not to: Railway's
  // edge overwrites that header on ingress with the peer IT saw (this proxy's
  // egress) and appends one internal hop, so whatever we write is discarded before
  // any handler sees it. That is why every visitor was landing in a rate-limit
  // bucket keyed on infrastructure.
  //
  // The secret is what makes the new header safe to believe: api.saqua.io is
  // publicly reachable, so without proof of origin anyone could post a client IP of
  // their choosing and mint unlimited fresh buckets. We send neither header when
  // the secret is unset, so the backend falls back to bucketing on this proxy's
  // address — over-restrictive for our visitors, never permissive.
  const ip = clientIp(request);
  const secret = process.env.SAQUA_PROXY_SECRET || "";
  // Never let a caller's own copy of these reach the backend alongside ours.
  headers.delete("x-saqua-client-ip");
  headers.delete("x-saqua-proxy-secret");
  if (ip && secret) {
    headers.set("x-saqua-client-ip", ip);
    headers.set("x-saqua-proxy-secret", secret);
  }
  // Forward the sandboxed-demo session token to the backend as an explicit header
  // (mirrors the X-Saqua-Client-IP pattern). The backend also accepts the
  // forwarded Cookie, but this is robust to any intermediary that strips cookies.
  // Never let a caller's own copy through alongside ours.
  headers.delete("x-saqua-demo-session");
  const demoToken = request.cookies.get("saqua_demo")?.value;
  if (demoToken) headers.set("x-saqua-demo-session", demoToken);
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

    const contentType = upstream.headers.get("content-type") || "application/octet-stream";
    const responseHeaders = new Headers();
    upstream.headers.forEach((value, key) => {
      if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
        responseHeaders.set(key, value);
      }
    });
    responseHeaders.set("content-type", contentType);
    responseHeaders.set("x-saqua-trace-id", traceId);

    // Set-Cookie must be re-emitted per cookie: Headers.forEach above coalesces
    // multiple Set-Cookie values into one comma-joined string, which corrupts
    // them. The demo-session mint sets the token + expiry cookies this way.
    const setCookies = upstream.headers.getSetCookie?.() ?? [];
    if (setCookies.length) {
      responseHeaders.delete("set-cookie");
      for (const c of setCookies) responseHeaders.append("set-cookie", c);
    }

    // Server-Sent Events (the live demo streams the pipeline as it runs): pass the
    // body straight through as a stream. Buffering it with arrayBuffer() — as we do
    // for normal JSON below — would withhold every event until the run finished,
    // defeating the point. content-encoding is hop-by-hop and already stripped, so
    // there is no half-buffered gzip layer to worry about.
    if (contentType.includes("text/event-stream")) {
      responseHeaders.set("cache-control", "no-cache, no-transform");
      responseHeaders.set("x-accel-buffering", "no");
      logProxy("info", "api_proxy_stream", {
        trace_id: traceId,
        method: request.method,
        path,
        target_url: logSafeUrl(url),
        backend_status: upstream.status,
        backend_content_type: contentType,
        duration_ms: Date.now() - started,
      });
      return new NextResponse(upstream.body, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: responseHeaders,
      });
    }

    const responseBytes = await upstream.arrayBuffer();
    const durationMs = Date.now() - started;

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
