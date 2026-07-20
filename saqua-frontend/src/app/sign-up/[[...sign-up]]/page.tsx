import Link from "next/link";
import { redirect } from "next/navigation";
import { SignUp } from "@clerk/nextjs";
import { AuthDiagnostics } from "@/components/auth/auth-diagnostics";
import { PRELAUNCH, WAITLIST_ANCHOR } from "@/lib/launch";

export default function SignUpPage() {
  // Closing signup means the ROUTE refuses, not just that the CTAs stopped
  // linking here. Anyone arriving from an old link, a bookmark, or a search
  // result gets the waitlist instead of a form that would create an account
  // nobody is ready to serve.
  if (PRELAUNCH) {
    redirect(WAITLIST_ANCHOR);
  }
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-4 py-10 text-text">
      <AuthDiagnostics />
      <div className="accent-glow fixed inset-0 opacity-35" />
      <div className="relative">
        <SignUp
          routing="path"
          path="/sign-up"
          signInUrl="/sign-in"
          forceRedirectUrl="/dashboard"
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
