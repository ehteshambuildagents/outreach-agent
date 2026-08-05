/**
 * Return-path safety, shared by the sign-in and sign-up pages so the checkout flow
 * can bounce a visitor through authentication and back to /pricing without ever
 * becoming an open redirect.
 *
 * A path is honored ONLY when it is same-origin and unambiguous: a single leading
 * slash, no protocol-relative "//host", no backslash trick, and no embedded
 * scheme. Anything else returns null, and the caller falls back to a safe default.
 */
export function safeInternalPath(raw: string | undefined | null): string | null {
  if (!raw) return null;
  if (!raw.startsWith("/")) return null; // must be an absolute path, not a bare host
  if (raw.startsWith("//")) return null; // protocol-relative → another origin
  if (raw.startsWith("/\\")) return null; // "/\evil.com" is treated as a host by browsers
  if (raw.includes("://")) return null; // an embedded scheme (http://, javascript:, …)
  if (raw.includes("\\")) return null; // reject backslashes outright
  return raw;
}

/**
 * Build an auth URL that carries a validated return path, so choosing "Sign in" vs
 * "Create account" from the pricing flow preserves where the visitor should land.
 */
export function withRedirect(base: string, redirectPath: string | null | undefined): string {
  const safe = safeInternalPath(redirectPath);
  return safe ? `${base}?redirect_url=${encodeURIComponent(safe)}` : base;
}
