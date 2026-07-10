/* Saqua auth — loads Clerk in the browser and wires the auth UI.

   Flow:
     1. Fetch /api/public-config -> the Clerk PUBLISHABLE key (never the secret).
     2. Hot-load clerk-js from the instance's Frontend API (derived from the key).
     3. Behave per <body data-saqua-mode="...">:
          "app"    -> require a session (redirect to /login.html if signed out),
                      mount the user button, and expose getToken() for API calls.
          "signin" -> if already signed in, go to the app; else mount Clerk's
                      Sign-in (or Sign-up) into #clerkAuth.

   Only the publishable key reaches the browser. The session JWT from
   Clerk.session.getToken() is what the workspace sends to the API, where it is
   verified server-side against Clerk's public keys. */

const APPEARANCE = {
  variables: {
    colorPrimary: '#3FD0BE',
    colorBackground: '#141416',
    colorText: '#ECECEE',
    colorTextSecondary: '#A7A8AE',
    colorInputBackground: '#191A1C',
    colorInputText: '#ECECEE',
    colorTextOnPrimaryBackground: '#04231F',
    colorDanger: '#E28A8A',
    borderRadius: '10px',
    fontFamily: "'Inter', -apple-system, system-ui, sans-serif",
  },
  elements: { card: { boxShadow: 'none' }, footer: { background: 'transparent' } },
};

function frontendApiHost(pk) {
  // pk_test_<b64> / pk_live_<b64> ; the base64 decodes to "<host>$"
  const body = pk.replace(/^pk_(test|live)_/, '');
  const padded = body + '='.repeat((4 - (body.length % 4)) % 4);
  return atob(padded).replace(/\$$/, '');
}

function loadClerkScript(host, pk) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.async = true;
    s.crossOrigin = 'anonymous';
    s.setAttribute('data-clerk-publishable-key', pk);
    s.src = `https://${host}/npm/@clerk/clerk-js@5/dist/clerk.browser.js`;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error('Failed to load Clerk.'));
    document.head.appendChild(s);
  });
}

async function initClerk() {
  let cfg = {};
  try { cfg = await (await fetch('/api/public-config')).json(); } catch (e) { /* offline */ }
  const pk = cfg && cfg.clerkPublishableKey;
  if (!pk) return null;                 // auth not configured -> caller decides
  await loadClerkScript(frontendApiHost(pk), pk);
  const Clerk = window.Clerk;
  // Global afterSignOutUrl so EVERY sign-out path (UserButton, explicit button)
  // reliably lands on the sign-in page (fixes logout in Clerk v5).
  await Clerk.load({ appearance: APPEARANCE, afterSignOutUrl: '/login.html' });
  return Clerk;
}

const qp = (name) => new URLSearchParams(location.search).get(name);

async function main() {
  const mode = (document.body && document.body.dataset.saquaMode) || 'app';

  // Always expose the auth helpers; they no-op until Clerk is ready/signed-in.
  window.saquaAuth = {
    getToken: async () =>
      (window.Clerk && window.Clerk.session) ? await window.Clerk.session.getToken() : null,
    // Reliable sign-out: clear the Clerk session, then land on sign-in even if
    // the global redirect didn't fire.
    signOut: async () => {
      if (!window.Clerk) { location.replace('/login.html'); return; }
      try { await window.Clerk.signOut(); } catch (e) { /* fall through to redirect */ }
      location.replace('/login.html');
    },
    // Real Clerk account deletion for the signed-in user, then sign out + home.
    deleteAccount: async () => {
      if (!(window.Clerk && window.Clerk.user)) throw new Error('Not signed in.');
      await window.Clerk.user.delete();
      try { await window.Clerk.signOut(); } catch (e) { /* already gone */ }
      location.replace('/index.html');
    },
  };

  let Clerk = null;
  try { Clerk = await initClerk(); } catch (e) { Clerk = null; }

  // Clerk not configured (no keys) -> fail open so local dev still runs.
  if (!Clerk) {
    if (window.__saquaResolveAuth) window.__saquaResolveAuth();
    return;
  }

  if (mode === 'signin') {
    if (Clerk.user) { location.replace('/app.html'); return; }
    const el = document.getElementById('clerkAuth');
    if (!el) return;
    const common = { appearance: APPEARANCE, fallbackRedirectUrl: '/app.html' };
    if (qp('mode') === 'signup') {
      Clerk.mountSignUp(el, Object.assign({ signInUrl: '/login.html' }, common));
    } else {
      Clerk.mountSignIn(el, Object.assign({ signUpUrl: '/login.html?mode=signup' }, common));
    }
    return;
  }

  // mode === 'app' — protected workspace.
  if (!Clerk.user) { location.replace('/login.html'); return; }
  const ub = document.getElementById('userButton');
  if (ub) {
    Clerk.mountUserButton(ub, { appearance: APPEARANCE, afterSignOutUrl: '/login.html' });
  }
  if (window.__saquaResolveAuth) window.__saquaResolveAuth();   // release the app
}

main();
