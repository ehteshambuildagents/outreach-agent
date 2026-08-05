import Link from "next/link";
import { redirect } from "next/navigation";
import { SignUp } from "@clerk/nextjs";
import { AuthDiagnostics } from "@/components/auth/auth-diagnostics";
import { PRELAUNCH, WAITLIST_ANCHOR } from "@/lib/launch";
import { safeInternalPath, withRedirect } from "@/lib/redirect";

export default function SignUpPage({
  searchParams,
}: {
  searchParams?: { redirect_url?: string };
}) {
  // The purchase funnel arrives with a validated same-origin return path
  // (/pricing?checkout=pro|max). That intent — someone who came to BUY — is what
  // reopens account creation. A visitor with no such intent while pre-launch is
  // still sent to the waitlist, so public signup isn't broadly reopened; only the
  // paid checkout flow can mint an account, and even then app access stays gated by
  // the middleware + backend approval + plan entitlements (an unpaid account gets
  // no product access). safeInternalPath rejects //, backslash and scheme redirects.
  const returnPath = safeInternalPath(searchParams?.redirect_url);
  if (!returnPath && PRELAUNCH) {
    redirect(WAITLIST_ANCHOR);
  }

  const redirectUrl = returnPath ?? "/dashboard";
  const signInHref = withRedirect("/sign-in", returnPath);

  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-4 py-10 text-text">
      <AuthDiagnostics />
      <div className="accent-glow fixed inset-0 opacity-35" />
      <div className="relative">
        <SignUp
          routing="path"
          path="/sign-up"
          signInUrl={signInHref}
          forceRedirectUrl={redirectUrl}
          appearance={{
            variables: {
              colorPrimary: "#4f5af7",
              colorBackground: "#ffffff",
              colorInputBackground: "#ffffff",
              colorInputText: "#111111",
              colorText: "#111111",
              colorTextSecondary: "#6c6d76",
              borderRadius: "10px",
            },
            elements: {
              cardBox: "border border-border bg-card shadow-card",
              footer: "hidden",
            },
          }}
        />
        <p className="mx-auto mt-4 max-w-xs text-center text-xs text-muted">
          Already have an account?{" "}
          <Link className="font-medium text-accent underline" href={signInHref}>
            Sign in
          </Link>
        </p>
        <p className="mx-auto mt-3 max-w-xs text-center text-xs text-muted">
          By creating an account you agree to our{" "}
          <Link className="underline" href="/terms">
            Terms of Service
          </Link>{" "}
          and{" "}
          <Link className="underline" href="/privacy">
            Privacy Policy
          </Link>
          .
        </p>
      </div>
    </main>
  );
}
