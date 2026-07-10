"""Streamlit web app — paste a company URL, get a researched, personalized cold
email. A thin UI over the existing agents (logic unchanged):

    research_company(url)  ->  write_email(research)  ->  shown on screen

SECURITY: the API key is read from the environment (local `.env` via
python-dotenv) or from Streamlit secrets (on Streamlit Cloud). It is never
hard-coded, never written to the page, and never logged. See DEPLOY.md.
"""

import html
import json
import os
import random
import re

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

st.set_page_config(
    page_title="Researched cold outreach",
    page_icon="✦",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Per-browser-session soft cap, so a shared public link can't quietly run up a
# big bill from one tab. Every click generates a FRESH email — AI output is
# never cached.
MAX_PER_SESSION = 15


# ── Secret loading (env first, then Streamlit secrets) ─────────────────
def _load_api_key() -> bool:
    """Ensure the API key is in the environment. Returns True if present.

    Order: a local `.env` (python-dotenv) or an already-set env var wins; on
    Streamlit Cloud we copy it out of st.secrets. The value is never displayed.
    """
    load_dotenv()  # no-op if there's no .env (e.g. on Streamlit Cloud)
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return True
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")  # raises if no secrets file
    except Exception:
        key = None
    if key and str(key).strip():
        os.environ["ANTHROPIC_API_KEY"] = str(key).strip()
        return True
    return False


_HAS_KEY = _load_api_key()

# Import the agents AFTER the key is in the environment.
from agents.research import research_company  # noqa: E402
from agents.writer import write_email          # noqa: E402


# ── Small helpers ──────────────────────────────────────────────────────
def _esc(value) -> str:
    """HTML-escape any value before it goes into raw HTML (XSS-safe: company /
    contact strings come from the researched site)."""
    return html.escape(str(value))


def _normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


_NOTICE_DOT = {"info": "var(--accent)", "warn": "#d8a13a", "error": "#d56b6b"}


def _notice(title: str, detail: str = "", tone: str = "info") -> None:
    """A calm, neutral status card (replaces the stock Streamlit alert boxes)."""
    dot = _NOTICE_DOT.get(tone, "var(--accent)")
    detail_html = f'<div class="ntc-d">{_esc(detail)}</div>' if detail else ""
    st.markdown(
        f'<div class="ntc"><span class="ntc-dot" style="background:{dot}"></span>'
        f'<div><div class="ntc-t">{_esc(title)}</div>{detail_html}</div></div>',
        unsafe_allow_html=True,
    )


# ── Styling (dark, premium SaaS — Linear / Vercel / Raycast register) ──
st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

      :root {
        --accent: #7c6cf6;
        --accent-2: #9a8bff;
        --ink: #ECECF1;
        --muted: #9aa3b2;
        --faint: #727a89;
        --border: rgba(255,255,255,0.08);
        --border-lit: rgba(255,255,255,0.14);
        --card: linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.018));
      }
      html, body, .stApp, [class*="css"] {
        font-family: 'Inter', -apple-system, 'Segoe UI', Roboto, sans-serif;
        color: var(--ink);
      }
      #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"],
      [data-testid="stStatusWidget"] { visibility: hidden; }
      .stApp {
        background:
          radial-gradient(900px 480px at 50% -12%, rgba(124,108,246,0.16), transparent 62%),
          radial-gradient(700px 380px at 88% 8%, rgba(80,120,255,0.07), transparent 60%),
          #08080A;
      }
      .block-container { max-width: 680px; padding-top: 4.6rem; padding-bottom: 5rem; }

      /* hero */
      .hero { margin-bottom: 2.4rem; }
      .hero-title {
        font-size: 2.7rem; font-weight: 800; line-height: 1.07; letter-spacing: -0.035em;
        margin: 0 0 1.05rem 0; max-width: 30rem;
        background: linear-gradient(176deg, #ffffff 30%, #aeb0c2);
        -webkit-background-clip: text; background-clip: text;
        -webkit-text-fill-color: transparent;
      }
      .hero-sub { color: var(--muted); font-size: 1.08rem; line-height: 1.62;
                  max-width: 50ch; }

      /* form: drop the default box, style the input + button */
      [data-testid="stForm"] { border: none; padding: 0; background: transparent; }
      [data-testid="stTextInput"] input {
        background: rgba(255,255,255,0.03); border: 1px solid var(--border);
        border-radius: 13px; padding: 0.95rem 1.05rem; font-size: 1rem; color: #fff;
        transition: border-color .15s ease, box-shadow .15s ease;
      }
      [data-testid="stTextInput"] input::placeholder { color: var(--faint); }
      [data-testid="stTextInput"] input:focus {
        border-color: rgba(124,108,246,0.7);
        box-shadow: 0 0 0 4px rgba(124,108,246,0.14);
      }
      .stButton > button, [data-testid="stFormSubmitButton"] > button {
        width: 100%; border: none; border-radius: 13px; padding: 0.9rem 1.25rem;
        font-weight: 600; font-size: 1rem; color: #fff; letter-spacing: -.01em;
        background: linear-gradient(180deg, #8275ee, #6a59e8);
        box-shadow: 0 6px 18px -10px rgba(124,108,246,0.55),
                    inset 0 1px 0 rgba(255,255,255,0.14);
        transition: transform .14s ease, filter .14s ease, box-shadow .14s ease;
      }
      .stButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover {
        transform: translateY(-1px); filter: brightness(1.05);
        box-shadow: 0 10px 24px -12px rgba(124,108,246,0.7),
                    inset 0 1px 0 rgba(255,255,255,0.18);
      }
      [data-testid="stToggle"] { margin-top: .35rem; }
      [data-testid="stToggle"] label { color: var(--muted) !important; font-size: .9rem; }

      /* cards (native bordered containers + custom .card) */
      [data-testid="stVerticalBlockBorderWrapper"], .card {
        border: 1px solid var(--border) !important; border-radius: 20px;
        background: var(--card); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
        padding: 1.5rem 1.6rem; margin-bottom: 1.1rem;
        box-shadow: 0 1px 0 rgba(255,255,255,0.05) inset,
                    0 28px 60px -38px rgba(0,0,0,0.75);
      }
      .card-label {
        font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.14em;
        color: var(--faint); font-weight: 600; margin-bottom: 1.05rem;
      }

      /* email card */
      .mail-head { display:flex; align-items:center; gap:.85rem; margin-bottom:1.05rem; }
      .mail-meta { flex:1; min-width:0; }
      .mail-to { color: var(--faint); font-size: .8rem; margin-bottom:.12rem; }
      .mail-subj { color: var(--ink); font-weight:600; font-size:1.02rem; letter-spacing:-.01em;
        white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .mail-badge { font-size:.68rem; text-transform:uppercase; letter-spacing:.1em;
        color:var(--accent-2); border:1px solid rgba(124,108,246,.4); border-radius:999px;
        padding:.2rem .55rem; background:rgba(124,108,246,.1); }
      .avatar { width:38px; height:38px; flex:0 0 38px; border-radius:11px; color:#fff;
        display:flex; align-items:center; justify-content:center; font-weight:700; font-size:1rem;
        background: linear-gradient(150deg, var(--accent-2), var(--accent));
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.3); }
      .stTextArea textarea {
        background: rgba(0,0,0,0.28) !important; border: 1px solid var(--border) !important;
        border-radius: 13px; font-size: 0.98rem; line-height: 1.62; color: #e9e9ee;
        padding: 1rem 1.1rem;
      }

      /* research report (grouped, analyst-summary register) */
      .rep-co { font-size:1.32rem; font-weight:700; letter-spacing:-.02em; color:var(--ink); }
      .rep-what { color:var(--muted); font-size:.96rem; line-height:1.55; margin-top:.4rem; }
      .rep-group { margin-top:1.3rem; padding-top:1.3rem; border-top:1px solid var(--border); }
      .rep-glabel { font-size:.66rem; text-transform:uppercase; letter-spacing:.14em;
        color:var(--faint); font-weight:600; margin-bottom:.8rem; }
      .rep-contact { display:flex; align-items:center; gap:.7rem; }
      .rep-cname { font-weight:600; font-size:.96rem; color:var(--ink); }
      .rep-crole { font-size:.83rem; color:var(--faint); margin-top:.05rem; }
      .rep-row { display:grid; grid-template-columns: 160px 1fr; gap:1rem;
        padding:.62rem 0; border-bottom:1px solid var(--border); }
      .rep-group .rep-row:last-child { border-bottom:none; padding-bottom:0; }
      .rep-k { color:var(--faint); font-size:.83rem; padding-top:.05rem; }
      .rep-v { color:var(--ink); font-size:.92rem; line-height:1.5; }
      .rep-angle { position:relative; padding:.5rem 0 .5rem 1.1rem; color:var(--ink);
        font-size:.92rem; line-height:1.5; border-bottom:1px solid var(--border); }
      .rep-group .rep-angle:last-child { border-bottom:none; padding-bottom:0; }
      .rep-angle::before { content:""; position:absolute; left:.1rem; top:.92rem;
        width:5px; height:5px; border-radius:50%; background:var(--accent); }
      .rep-foot { color:var(--faint); font-size:.78rem; margin-top:1.3rem; padding-top:1.1rem;
        border-top:1px solid var(--border); display:flex; align-items:center; gap:.45rem; }
      .rep-dot { width:6px; height:6px; border-radius:50%; background:#3fbf7f;
        box-shadow:0 0 0 3px rgba(63,191,127,.14); }
      @media (max-width: 560px) { .rep-row { grid-template-columns: 1fr; gap:.15rem; } }

      /* notices (calm, replacing stock Streamlit alerts) */
      .ntc { display:flex; gap:.7rem; align-items:flex-start; border:1px solid var(--border);
        background: rgba(255,255,255,0.025); border-radius:14px; padding:.95rem 1.1rem;
        margin-bottom:1.1rem; }
      .ntc-dot { width:8px; height:8px; border-radius:50%; margin-top:.45rem; flex:0 0 8px; }
      .ntc-t { color:var(--ink); font-size:.92rem; font-weight:500; line-height:1.45; }
      .ntc-d { color:var(--muted); font-size:.86rem; margin-top:.3rem; line-height:1.55; }

      .trust { color: var(--faint); font-size: .82rem; margin-top: .9rem; }
      .disclaimer { text-align: center; color: #5d636f; font-size: 0.76rem; margin-top: 3.5rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Hero ───────────────────────────────────────────────────────────────
st.markdown(
    """
    <div class="hero">
      <div class="hero-title">We read the company before we write the email.</div>
      <div class="hero-sub">Most tools generate the email from a prompt. This one
      researches the company's live website first: what they do, who to reach,
      why it matters. Then it writes only from what it actually finds.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

if not _HAS_KEY:
    _notice(
        "Workspace not configured",
        "An operator needs to add the API key to the app's secrets before it can "
        "run. See DEPLOY.md.",
        tone="error",
    )
    st.stop()


# ── Input form ─────────────────────────────────────────────────────────
with st.form("generate", clear_on_submit=False):
    url_in = st.text_input(
        "Company website",
        placeholder="stripe.com",
        label_visibility="collapsed",
    )
    submitted = st.form_submit_button(
        "Research the company", type="primary", use_container_width=True
    )
    add_reveal = st.toggle(
        "Append an AI-disclosure line",
        value=False,
        help="Adds a short P.S. noting the email was written with AI assistance.",
    )

st.markdown(
    '<div class="trust">Only public website content is read. '
    "Nothing is stored, and nothing is sent on your behalf.</div>",
    unsafe_allow_html=True,
)


# ── Loading experience (client-side staged animation; no engine change) ─
# Generic research ACTIONS (never invented findings) that mirror the real
# pipeline. The middle actions are sampled per run so repeated runs don't show
# the exact same sequence; the first and last steps stay anchored.
_STAGE_FIRST = "Fetching the homepage"
_STAGE_MIDDLE = [
    "Reading the about page",
    "Reading the pricing page",
    "Reviewing product pages",
    "Scanning team and leadership pages",
    "Identifying the decision-maker",
    "Understanding their positioning",
    "Looking for what sets them apart",
    "Reading recent updates",
    "Pulling out personalization angles",
]
_STAGE_LAST = ["Drafting the email", "Final review"]


def _build_stages() -> list:
    chosen = set(random.sample(_STAGE_MIDDLE, k=6))
    middle = [s for s in _STAGE_MIDDLE if s in chosen]  # keep a sensible order
    return [_STAGE_FIRST] + middle + _STAGE_LAST

_LOADER_TMPL = """<!doctype html><html><head><meta charset="utf-8"><style>
  *{box-sizing:border-box}
  body{margin:0;font-family:'Inter',-apple-system,'Segoe UI',Roboto,sans-serif;background:transparent}
  .wrap{border:1px solid rgba(255,255,255,0.09);border-radius:20px;
    background:linear-gradient(180deg,rgba(255,255,255,0.05),rgba(255,255,255,0.018));
    padding:1.4rem 1.55rem}
  .hd{display:flex;align-items:center;gap:.6rem;margin-bottom:1.05rem}
  .pulse{width:9px;height:9px;border-radius:50%;background:#7c6cf6;animation:p 1.6s infinite}
  @keyframes p{0%{box-shadow:0 0 0 0 rgba(124,108,246,.55)}70%{box-shadow:0 0 0 8px rgba(124,108,246,0)}100%{box-shadow:0 0 0 0 rgba(124,108,246,0)}}
  .t{color:#ECECF1;font-weight:600;font-size:.95rem;letter-spacing:-.01em}
  .stg{display:flex;align-items:center;gap:.7rem;padding:.36rem 0;opacity:.38;transition:opacity .45s ease}
  .stg.active{opacity:1}.stg.done{opacity:.82}
  .ic{width:18px;height:18px;position:relative;flex:0 0 18px}
  .dot{position:absolute;inset:5px;border-radius:50%;background:#3a3a46}
  .spin{position:absolute;inset:0;border:2px solid rgba(124,108,246,.25);border-top-color:#7c6cf6;border-radius:50%;opacity:0;animation:s .7s linear infinite}
  .chk{position:absolute;inset:0;color:#8b7bf8;font-size:13px;line-height:18px;text-align:center;opacity:0;transform:scale(.5);transition:all .3s ease}
  .stg.active .dot{opacity:0}.stg.active .spin{opacity:1}
  .stg.done .dot{opacity:0}.stg.done .spin{opacity:0}.stg.done .chk{opacity:1;transform:scale(1)}
  @keyframes s{to{transform:rotate(360deg)}}
  .lbl{color:#cdd0d8;font-size:.92rem}.stg.active .lbl{color:#fff}
</style></head><body>
  <div class="wrap">
    <div class="hd"><span class="pulse"></span><span class="t">Researching the company…</span></div>
    __ROWS__
  </div>
  <script>
    const n=__N__;let i=0;
    function set(idx){for(let k=0;k<n;k++){const e=document.getElementById('stg'+k);
      e.className='stg'+(k<idx?' done':(k===idx?' active':''));}}
    set(0);
    const iv=setInterval(()=>{i++;if(i>=n-1){set(n-1);clearInterval(iv);}else{set(i);}},2200);
  </script>
</body></html>"""


def _loader_html(stages: list) -> str:
    rows = "".join(
        '<div class="stg" id="stg%d"><span class="ic"><span class="dot"></span>'
        '<span class="spin"></span><span class="chk">&#10003;</span></span>'
        '<span class="lbl">%s</span></div>' % (i, _esc(s))
        for i, s in enumerate(stages)
    )
    return _LOADER_TMPL.replace("__ROWS__", rows).replace("__N__", str(len(stages)))


# ── Renderers ──────────────────────────────────────────────────────────
def _render_email(email: dict) -> None:
    subject = email.get("subject") or ""
    body = email.get("body") or ""
    to = email.get("to")
    company = email.get("company") or ""
    full_email = f"Subject: {subject}\n\n{body}"
    recipient = " · ".join(x for x in [to, company] if x) or "your prospect"
    initial = (to or company or "?").strip()[:1].upper() or "?"

    with st.container(border=True):
        st.markdown(
            f"""
            <div class="card-label">Email</div>
            <div class="mail-head">
              <div class="avatar">{_esc(initial)}</div>
              <div class="mail-meta">
                <div class="mail-to">To {_esc(recipient)}</div>
                <div class="mail-subj">{_esc(subject or "(no subject)")}</div>
              </div>
              <div class="mail-badge">Draft</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        # Editable composer (Markdown/LaTeX-safe so "$1M ARR" renders literally).
        st.text_area(
            "email", value=full_email, height=240, label_visibility="collapsed"
        )
        _copy_button(full_email)
        if email.get("used_reveal"):
            st.caption("Includes a short AI-disclosure note.")
        st.caption("Edit inline, then copy. Nothing is sent on your behalf.")


def _copy_button(text: str, label: str = "Copy email") -> None:
    """One-click, client-side clipboard copy of the full email (subject + body)."""
    # json.dumps escapes quotes/newlines; the extra replace stops an email body
    # containing "</script>" from breaking out of the <script> tag below.
    payload = json.dumps(text).replace("</", "<\\/")
    components.html(
        f"""
        <style>
          .copy-btn {{
            width: 100%; padding: 0.62rem 1rem; font-size: 0.92rem; font-weight: 600;
            color: #cdd0e6; background: rgba(124,108,246,0.12);
            border: 1px solid rgba(124,108,246,0.42); border-radius: 11px;
            cursor: pointer; transition: all 0.15s ease; letter-spacing:-.01em;
            font-family: 'Inter', -apple-system, "Segoe UI", Roboto, sans-serif;
          }}
          .copy-btn:hover {{ background: rgba(124,108,246,0.22); color:#fff; }}
          .copy-btn.copied {{ background: rgba(34,180,110,0.18); color:#7ee3ad;
                              border-color: rgba(34,180,110,0.5); }}
        </style>
        <button class="copy-btn" id="cp">{label}</button>
        <script>
          const btn = document.getElementById("cp");
          const text = {payload};
          const orig = btn.textContent;
          btn.addEventListener("click", async () => {{
            try {{
              await navigator.clipboard.writeText(text);
            }} catch (e) {{
              const ta = document.createElement("textarea");
              ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
              document.body.appendChild(ta); ta.focus(); ta.select();
              try {{ document.execCommand("copy"); }} catch (_e) {{}}
              document.body.removeChild(ta);
            }}
            btn.textContent = "Copied to clipboard"; btn.classList.add("copied");
            setTimeout(() => {{ btn.textContent = orig; btn.classList.remove("copied"); }}, 1800);
          }});
        </script>
        """,
        height=44,
    )


def _render_research(research: dict) -> None:
    # NOTE: research_score stays in `research` for internal use; we deliberately
    # do NOT surface the numeric score to users.
    data = research.get("data") or {}
    company = data.get("company_name") or "This company"
    contact = data.get("primary_contact_name") or data.get("founder_name")
    role = data.get("primary_contact_role") or data.get("founder_role") or "Primary contact"
    what = data.get("what_they_do")

    rows = []

    def add(label, value):
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v).strip() for v in value if str(v).strip())
        if value and str(value).strip():
            rows.append((label, str(value).strip()))

    add("Who they serve", data.get("target_customer"))
    add("Recent focus", data.get("recent_focus"))
    add("Traction", data.get("metrics_or_traction"))
    add("Mission", data.get("their_mission_or_why"))
    add("Pricing", data.get("pricing_model"))
    add("Notable customers", data.get("notable_customers"))
    add("Tech", data.get("tech_stack"))
    team = data.get("team_members") or []
    if team:
        names = ", ".join(
            (m.get("name", "") + (f" ({m['role']})" if m.get("role") else ""))
            for m in team if m.get("name")
        )
        add("Team", names)

    # Personalization angles (the verified hooks) — surfaced, never invented.
    angles = []
    if data.get("unique_hook"):
        angles.append(str(data["unique_hook"]).strip())
    for hook in data.get("additional_hooks") or []:
        if str(hook).strip():
            angles.append(str(hook).strip())
    angles = list(dict.fromkeys(angles))[:5]

    groups = []
    if contact:
        initial = _esc(str(contact).strip()[:1].upper() or "?")
        groups.append(("Decision-maker",
            f'<div class="rep-contact"><div class="avatar">{initial}</div>'
            f'<div><div class="rep-cname">{_esc(contact)}</div>'
            f'<div class="rep-crole">{_esc(role)}</div></div></div>'))
    if rows:
        grid = "".join(
            f'<div class="rep-row"><div class="rep-k">{_esc(k)}</div>'
            f'<div class="rep-v">{_esc(v)}</div></div>' for k, v in rows
        )
        groups.append(("Business summary", grid))
    if angles:
        items = "".join(f'<div class="rep-angle">{_esc(a)}</div>' for a in angles)
        groups.append(("Personalization opportunities", items))

    groups_html = "".join(
        f'<div class="rep-group"><div class="rep-glabel">{_esc(label)}</div>{content}</div>'
        for label, content in groups
    )
    what_html = f'<div class="rep-what">{_esc(what)}</div>' if what else ""
    pages = research.get("pages_crawled") or []
    foot = (f"Verified from {len(pages)} page(s) read on their site"
            if pages else "Verified from their public site")

    st.markdown(
        f"""
        <div class="card">
          <div class="card-label">Research report</div>
          <div class="rep-co">{_esc(company)}</div>
          {what_html}
          {groups_html}
          <div class="rep-foot"><span class="rep-dot"></span>{_esc(foot)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── Run ────────────────────────────────────────────────────────────────
if submitted:
    url = _normalize_url(url_in)
    if not url:
        _notice("Enter a company website above to begin.")
    elif st.session_state.get("runs", 0) >= MAX_PER_SESSION:
        _notice("Session limit reached", "Refresh the page to start over.", tone="warn")
    else:
        loader = st.empty()
        # Direct, single call to each agent — no caching, so every run is fresh.
        try:
            with loader:
                stages = _build_stages()
                components.html(_loader_html(stages),
                                height=140 + len(stages) * 30, scrolling=False)
            research = research_company(url)
            email = None
            if research.get("status") != "error":
                email = write_email(research, add_reveal=add_reveal)
        except Exception:  # noqa: BLE001 - never show a raw traceback to a user
            loader.empty()
            _notice("Something went wrong",
                    "We couldn't research that site just now. Please try a different URL.",
                    tone="error")
            st.stop()
        loader.empty()

        st.session_state["runs"] = st.session_state.get("runs", 0) + 1
        rstatus = research.get("status")
        _skip_detail = ("There wasn't enough company-specific information on that "
                        "site to write a grounded email. Try their main site.")

        if rstatus == "error":
            _notice("Couldn't research that site",
                    f"{research.get('error')} Check the URL, or try the company's "
                    "main marketing site.", tone="error")
        elif rstatus == "skip":
            _notice("Not enough to personalize", _skip_detail, tone="warn")
            _render_research(research)
        else:
            if email.get("status") == "ok":
                _render_email(email)
                _render_research(research)
            elif email.get("status") == "skip":
                _notice("Not enough to personalize", _skip_detail, tone="warn")
                _render_research(research)
            else:
                _notice("Couldn't draft the email",
                        "The research worked, but the email couldn't be written "
                        "just now. Please try again in a moment.", tone="warn")
                _render_research(research)


# ── Disclaimer (subtle, always at the bottom) ──────────────────────────
st.markdown(
    '<div class="disclaimer">AI can occasionally miss or misinterpret details. '
    "Review every email before sending.</div>",
    unsafe_allow_html=True,
)
