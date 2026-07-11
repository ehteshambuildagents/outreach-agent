import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

const isPublicRoute = createRouteMatcher([
  "/",
  "/pricing(.*)",
  "/sign-in(.*)",
  "/sign-up(.*)",
]);

export default clerkMiddleware(
  async (auth, request) => {
    if (isPublicRoute(request)) {
      return;
    }

    const { userId, redirectToSignIn } = await auth();
    if (!userId) {
      return redirectToSignIn({ returnBackUrl: request.url });
    }
  },
  {
    // Clerk's bot protection uses Cloudflare Turnstile. Let Clerk inject the
    // CSP directives it needs, including challenges.cloudflare.com for scripts
    // and frames, so CAPTCHA does not depend on an external edge header.
    contentSecurityPolicy: {},
  },
);

export const config = {
  matcher: ["/((?!api|_next|.*\\..*).*)"],
};
