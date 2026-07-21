# ⚠️ TEMPORARY reviewer access — RE-SEAL after Google OAuth approval

This repo currently has a **temporary access hole open for Google's OAuth
verification review** (Gmail `gmail.metadata` scope). It lets the reviewer test
account `support@saqua.io` log in and reach the app while the site is otherwise
pre-launch / locked down.

**Once Google approves the OAuth verification, close all of this.** Do not leave
reviewer access sitting open indefinitely.

Opened: 2026-07-21. Owner: founder.

---

## Re-seal checklist

### 1. `saqua-frontend/src/middleware.ts`
- [ ] Remove `"/sign-in(.*)"` from the `isPublicRoute` list.
- [ ] Remove the `isSignInRoute` matcher **and** its first-checked guard block
      (`if (isSignInRoute(request)) return;`) at the top of the handler.
- [ ] Remove the `hasAppAccess()` function and the
      `if (userId && hasAppAccess(sessionClaims)) return;` block; restore the
      plain "not public → redirect to `/`" behavior.
- [ ] Revert the top comment block back to the plain pre-launch lockdown
      description (no review-access exception).

  Net result: `middleware.ts` returns to the sealed state where every app page
  **and** `/sign-in` / `/sign-up` redirect to `/` for everyone.

### 2. Clerk Dashboard
- [ ] Clear the `appAccess` flag from `support@saqua.io`'s **publicMetadata**.
- [ ] Remove the `appAccess` claim from the session-token template
      (Dashboard → Sessions → Edit session token) if nothing else uses it.

### 3. DB approved-users store
- [ ] Revoke `support@saqua.io`'s approval in the access-control store
      (the one `require_approved_user` checks server-side).

### 4. Reviewer account (optional but recommended)
- [ ] Delete or disable the `support@saqua.io` Clerk user once the demo/video
      and Google's review are fully complete.

### 5. Verify sealed
- [ ] `https://www.saqua.io/sign-in` redirects to `/` (no login form renders).
- [ ] No app page (`/dashboard`, `/connections`, `/settings`) is reachable.
- [ ] Delete this file.

---

Related code anchor: `saqua-frontend/src/middleware.ts` (search for
`Review-access exception` and `isSignInRoute`).
