import Link from "next/link";
import { SignIn } from "@clerk/nextjs";
import { AuthDiagnostics } from "@/components/auth/auth-diagnostics";
import { safeInternalPath, withRedirect } from "@/lib/redirect";

export default function SignInPage({
  searchParams,
}: {
  searchParams?: { redirect_url?: string };
}) {
  // The validated same-origin return path (e.g. /pricing?checkout=pro). Kept nullable
  // for the cross-link so a plain visit doesn't invent one; auth itself falls back to
  // /dashboard. safeInternalPath rejects //, backslash and protocol-based redirects.
  const returnPath = safeInternalPath(searchParams?.redirect_url);
  const redirectUrl = returnPath ?? "/dashboard";
  // Carry the return path onto "Create account" so a brand-new customer who picks
  // sign-up instead is still returned to /pricing to resume the same checkout.
  const signUpHref = withRedirect("/sign-up", returnPath);

  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-4 py-10 text-text">
      <AuthDiagnostics />
      <div className="accent-glow fixed inset-0 opacity-35" />
      <div className="relative">
        <SignIn
          routing="path"
          path="/sign-in"
          signUpUrl={signUpHref}
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
          New to Saqua?{" "}
          <Link className="font-medium text-accent underline" href={signUpHref}>
            Create account
          </Link>
        </p>
      </div>
    </main>
  );
}
