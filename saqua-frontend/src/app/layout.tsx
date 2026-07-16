import { Analytics } from "@vercel/analytics/next";
import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
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
      <html lang="en" className="dark">
        <head>
          {/* Fonts load at runtime so production builds never fetch Google Fonts.
              Geist (sans) + Geist Mono (data) + Newsreader (upright serif, no italic
              axis requested — reserved for Saqua's generated email copy). */}
          <link rel="preconnect" href="https://fonts.googleapis.com" />
          <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
          <link
            href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500&family=Newsreader:opsz@6..72&display=swap"
            rel="stylesheet"
          />
        </head>
       <body className="min-h-screen bg-bg font-sans text-text antialiased">
  {children}
  <Analytics />
</body>
      </html>
    </ClerkProvider>
  );
}
