/* Saqua — shared site chrome (nav + footer), icons, and light interactions.
   Framework-free "components": one source of truth for nav/footer, injected
   into every marketing page so markup stays DRY. No data leaves the page. */

const NAV = `
<nav class="nav">
  <div class="container nav-inner">
    <a class="wordmark" href="/index.html">Saqua</a>
    <div class="nav-links">
      <a href="/index.html#how">How it works</a>
      <a href="/pricing.html">Pricing</a>
      <a href="/security.html">Security</a>
      <a href="/about.html">About</a>
    </div>
    <div class="nav-cta">
      <a class="btn btn-ghost" href="/login.html">Log in</a>
      <a class="btn btn-secondary" href="/book-demo.html">Book demo</a>
    </div>
    <button class="nav-burger" aria-label="Menu" data-menu><i data-lucide="menu"></i></button>
  </div>
</nav>`;

const FOOTER = `
<footer class="footer">
  <div class="container">
    <div class="footer-grid">
      <div>
        <div class="wordmark" style="margin-bottom:12px">Saqua</div>
        <p class="muted" style="font-size:14px;max-width:30ch">Researched outbound that reads like a founder wrote it — not a template.</p>
      </div>
      <div>
        <h4>Product</h4>
        <a href="/index.html#how">How it works</a>
        <a href="/pricing.html">Pricing</a>
        <a href="/app.html">Workspace</a>
        <a href="/book-demo.html">Book a demo</a>
      </div>
      <div>
        <h4>Company</h4>
        <a href="/about.html">About</a>
        <a href="/security.html">Security</a>
        <a href="/contact.html">Contact</a>
      </div>
      <div>
        <h4>Legal</h4>
        <a href="/privacy.html">Privacy</a>
        <a href="/terms.html">Terms</a>
      </div>
      <div>
        <h4>Get started</h4>
        <a href="/login.html">Log in</a>
        <a href="/book-demo.html">Book demo</a>
      </div>
    </div>
    <div class="footer-bottom">
      <span>© ${new Date().getFullYear()} Saqua</span>
      <span>Built for teams that hate templates.</span>
    </div>
  </div>
</footer>`;

// Clerk sets a readable `__client_uat` cookie: "0" when signed out, a non-zero
// timestamp when a session exists. We use it for an instant, no-network auth
// check so an authenticated visitor is never shown "Log in" (issue #2). Full
// verification still happens server-side on every API call.
function isSignedIn(){
  const m = document.cookie.match(/(?:^|;)\s*__client_uat=([^;]*)/);
  return !!(m && m[1] && m[1] !== '0');
}

function reflectAuth(scope){
  if (!isSignedIn()) return;
  (scope || document).querySelectorAll('a[href="/login.html"]').forEach(a => {
    a.setAttribute('href', '/app.html');
    if (a.textContent.trim().toLowerCase() === 'log in') a.textContent = 'Open app';
  });
}

function mountChrome(){
  const nav = document.querySelector('[data-nav]');
  if (nav){
    nav.innerHTML = NAV;
    const here = location.pathname.replace(/\/$/, '') || '/index.html';
    nav.querySelectorAll('.nav-links a').forEach(a => {
      const href = a.getAttribute('href').split('#')[0];
      if (href && (here === href || (href !== '/index.html' && here.endsWith(href)))) a.classList.add('active');
    });
  }
  const footer = document.querySelector('[data-footer]');
  if (footer) footer.innerHTML = FOOTER;
  reflectAuth();   // swap "Log in" -> "Open app" for authenticated visitors
}

function initMenu(){
  const burger = document.querySelector('[data-menu]');
  const links = document.querySelector('.nav-links');
  if (!burger || !links) return;
  burger.addEventListener('click', () => {
    const open = links.style.display === 'flex';
    links.style.display = open ? 'none' : 'flex';
    links.style.position = 'absolute'; links.style.top = 'var(--nav-h)';
    links.style.left = '0'; links.style.right = '0'; links.style.flexDirection = 'column';
    links.style.background = 'var(--panel)'; links.style.borderBottom = '1px solid var(--border)';
    links.style.padding = '12px 20px';
  });
}

function initFaq(){
  document.querySelectorAll('.faq-item').forEach(item => {
    const q = item.querySelector('.faq-q');
    const a = item.querySelector('.faq-a');
    if (!q || !a) return;
    q.addEventListener('click', () => {
      const open = item.classList.toggle('open');
      a.style.maxHeight = open ? a.scrollHeight + 'px' : '0';
    });
  });
}

function initReveal(){
  const els = Array.from(document.querySelectorAll('.reveal'));
  const show = e => e.classList.add('in');
  // Fail-open: reveal anything already in view, and guarantee everything shows
  // even if IntersectionObserver never fires (content must never stay hidden).
  const vh = window.innerHeight || 800;
  els.forEach(e => { if (e.getBoundingClientRect().top < vh * 0.95) show(e); });
  if (!('IntersectionObserver' in window)){ els.forEach(show); return; }
  const io = new IntersectionObserver((entries) => {
    entries.forEach(e => { if (e.isIntersecting){ show(e.target); io.unobserve(e.target); } });
  }, { threshold:.12, rootMargin:'0px 0px -6% 0px' });
  els.forEach(e => io.observe(e));
  setTimeout(() => els.forEach(show), 1400); // safety net
}

document.addEventListener('DOMContentLoaded', () => {
  mountChrome();
  if (window.lucide) lucide.createIcons();
  initMenu(); initFaq(); initReveal();
});
