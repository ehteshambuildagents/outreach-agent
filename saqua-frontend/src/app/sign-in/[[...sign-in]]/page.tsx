import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-4 py-10 text-text">
      <div className="accent-glow fixed inset-0 opacity-35" />
      <div className="relative">
        <SignIn
          routing="path"
          path="/sign-in"
          signUpUrl="/sign-up"
          forceRedirectUrl="/dashboard"
          appearance={{
            variables: {
              colorPrimary: "#7c5cff",
              colorBackground: "#111116",
              colorInputBackground: "rgba(255,255,255,0.03)",
              colorInputText: "#f4f2ff",
              colorText: "#f4f2ff",
              colorTextSecondary: "#9b96ad",
              borderRadius: "8px",
            },
            elements: {
              cardBox: "border border-border bg-panel shadow-card",
              footer: "hidden",
            },
          }}
        />
      </div>
    </main>
  );
}
