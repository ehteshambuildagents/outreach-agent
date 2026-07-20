import { Analytics } from "@vercel/analytics/next";
import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { VisibilityRepaint } from "@/components/shell/visibility-repaint";
import "./globals.css";

export const metadata: Metadata = {
  title: "Saqua - The AI SDR for founders",
  description:
    "Saqua finds the right founders, writes personalized outreach, and automates follow-ups that actually get replies.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider
      signInUrl="/sign-in"
      signUpUrl="/sign-up"
      signInFallbackRedirectUrl="/ai"
      signUpFallbackRedirectUrl="/ai"
    >
      <html lang="en">
        <head>
          {/* Fonts load at runtime so production builds never fetch fonts at build.
              General Sans (display) via Fontshare; Inter (body/UI), Geist Mono (data),
              Newsreader (upright serif — reserved for Saqua's generated email copy). */}
          <link rel="preconnect" href="https://fonts.googleapis.com" />
          <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
          <link rel="preconnect" href="https://api.fontshare.com" crossOrigin="anonymous" />
          <link
            href="https://api.fontshare.com/v2/css?f[]=general-sans@400,500,600,700&display=swap"
            rel="stylesheet"
          />
          <link
            href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Geist+Mono:wght@400;500&family=Newsreader:opsz@6..72&display=swap"
            rel="stylesheet"
          />
        </head>
       {/* No bg-bg here — <html> owns the canvas colour (see globals.css). An opaque
           body background paints over every -z-10 glow. */}
       <body className="min-h-screen font-sans text-text antialiased">
  <VisibilityRepaint />
  {children}
  <Analytics />
</body>
      </html>
    </ClerkProvider>
  );
}
