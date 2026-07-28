import { Analytics } from "@vercel/analytics/next";
import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { VisibilityRepaint } from "@/components/shell/visibility-repaint";
import "./globals.css";

export const metadata: Metadata = {
  title: "Saqua - researched outbound for founders",
  description:
    "Saqua finds companies worth contacting, reads what each one has published, and writes the first email around a real detail it found. You approve every send.",
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
          {/* Scroll reveals server-render hidden and are un-hidden by script. With
              no script that never happens, so the page would render blank — the
              copy is all there in the HTML, just at opacity 0. Show it instead. */}
          <noscript>
            {/* Set as raw HTML on purpose: as a JSX child React would escape the
                quotes to &quot;, and a <style> body is raw text, so the escaped
                selector would never match. The string is a literal, not input. */}
            <style
              dangerouslySetInnerHTML={{
                __html: `[style*="opacity:0"]{opacity:1!important;transform:none!important}`,
              }}
            />
          </noscript>
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
