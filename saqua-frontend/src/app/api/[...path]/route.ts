import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

const API_ORIGIN = process.env.SAQUA_API_ORIGIN || "http://127.0.0.1:8000";
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

function logProxy(level: "info" | "error", event: string, data: Record<string, unknown>) {
  const payload = JSON.stringify({ event, ...data });
  if (level === "error") {
    console.error(payload);
  } else {
    console.info(payload);
  }
}

function forwardedHeaders(request: NextRequest, traceId: string): Headers {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    const normalized = key.toLowerCase();
    if (!HOP_BY_HOP_HEADERS.has(normalized)) {
      headers.set(key, value);
    }
  });
  headers.set("x-forwarded-host", request.headers.get("host") || "");
  headers.set("x-forwarded-proto", request.nextUrl.protocol.replace(":", ""));
  headers.set("x-request-id", traceId);
  headers.set("x-saqua-proxy-trace-id", traceId);
  return headers;
}

async function proxy(request: NextRequest, { params }: { params: { path: string[] } }) {
  const started = Date.now();
  const traceId = randomUUID();
  const path = `/api/${(params.path || []).join("/")}`;
  const url = backendUrl(params.path || [], request.nextUrl.search);
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new Error("Backend request timed out")), PROXY_TIMEOUT_MS);

  logProxy("info", "api_proxy_start", {
    trace_id: traceId,
    method: request.method,
    path,
    target_url: url,
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
      target_url: url,
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
      target_url: url,
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
