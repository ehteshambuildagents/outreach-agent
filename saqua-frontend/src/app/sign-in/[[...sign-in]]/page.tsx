import { SignIn } from "@clerk/nextjs";
import { AuthDiagnostics } from "@/components/auth/auth-diagnostics";

export default function SignInPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-4 py-10 text-text">
      <AuthDiagnostics />
      <div className="accent-glow fixed inset-0 opacity-35" />
      <div className="relative">
        <SignIn
          routing="path"
          path="/sign-in"
          signUpUrl="/sign-up"
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
      </div>
    </main>
  );
}
