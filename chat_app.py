"""Saqua — conversational workspace UI (front end only).

A premium, three-column dashboard-style chat interface over the existing
agents. This file is PRESENTATION ONLY: it renders the same conversations and
calls the same backend (`chat.agent.respond`, `chat.store`) — no research,
chat, or tool logic lives here and none of it is modified.

    chat.agent.respond(conversation, user_text, store)  ->  tools do the work

The right-hand "Research Summary" / "Sources" / "Contact" panel and the
sidebar's row timestamps are built ENTIRELY from data the backend already
returns (``conversation.workspace["research"]`` / ``["email"]``) — nothing is
invented. Fields the research engine doesn't extract (e.g. HQ address, a
verified LinkedIn URL) are simply omitted rather than guessed.

SECURITY: the API key is read from the environment (local ``.env``) or
Streamlit secrets; it is never hard-coded, shown, or logged. All user-supplied
and model-supplied text is HTML-escaped before being placed in raw HTML
(``_esc``); external links carry rel="noopener noreferrer".
"""

import html
import json
import os
import time
from urllib.parse import quote, urlparse

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

st.set_page_config(page_title="Saqua", page_icon="◇",
                   layout="wide", initial_sidebar_state="expanded")

# Soft per-session cap so a shared public link can't run up a big bill.
MAX_MESSAGES_PER_SESSION = 60


def _load_api_key() -> bool:
    load_dotenv()
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return True
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        key = None
    if key and str(key).strip():
        os.environ["ANTHROPIC_API_KEY"] = str(key).strip()
        return True
    return False


_HAS_KEY = _load_api_key()

# Import chat layer AFTER the key is in the environment.
from chat import agent, tools  # noqa: E402
from chat.models import Conversation, EMAIL, NOTICE, RESEARCH  # noqa: E402
from chat.store import ConversationStore  # noqa: E402


# ── Theme (all styling lives here; no backend impact) ──────────────────
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;450;500;600;700&display=swap');

:root{
  --bg:#0b0c0e; --elev:#101116; --card:#15161b; --card2:#1b1c22;
  --bd:rgba(255,255,255,.07); --bd2:rgba(255,255,255,.13);
  --text:#eceef1; --dim:#9a9fa8; --faint:#666b74;
  --accent:#6D5EF7; --accent-2:#8677ff;
  --accent-soft:rgba(109,94,247,.14); --accent-bd:rgba(109,94,247,.36);
  --good:#34d399; --good-soft:rgba(52,211,153,.14); --good-bd:rgba(52,211,153,.34);
  --radius:16px; --shadow:0 14px 40px rgba(0,0,0,.44);
}

html, body, [class*="css"]{
  font-family:'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  color:var(--text); -webkit-font-smoothing:antialiased;
}
.stApp, [data-testid="stAppViewContainer"]{
  background-color:var(--bg) !important;
  background-image:radial-gradient(1000px 480px at 50% -12%, rgba(109,94,247,.06), transparent 60%);
  background-attachment:fixed !important;
}
[data-testid="stHeader"], #MainMenu, header, footer,
[data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"]{ display:none !important; }

[data-testid="stMainBlockContainer"], .block-container{
  max-width:1120px !important; padding-top:0 !important; padding-bottom:9rem !important;
}

@keyframes sqrise{ from{opacity:0; transform:translateY(6px);} to{opacity:1; transform:none;} }

/* ── Sidebar ─────────────────────────────────────────────────────── */
section[data-testid="stSidebar"]{
  background:#111217 !important; border-right:1px solid var(--bd); width:288px !important;
}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"]{ padding:18px 12px 12px; }
section[data-testid="stSidebar"] [data-testid="stSidebarHeader"]{ padding:0; height:0; }

.brand{ font-weight:700; font-size:22px; letter-spacing:-.02em; color:var(--accent-2);
        padding:2px 8px 14px; }
.side-label{ font-size:10.5px; letter-spacing:.1em; text-transform:uppercase;
   color:var(--faint); padding:14px 12px 8px; font-weight:600; }
.side-empty{ color:var(--faint); font-size:13px; padding:12px; line-height:1.5; }

/* Primary CTA: solid accent fill (keyed container .st-key-new_chat) */
.st-key-new_chat button{
  background:var(--accent) !important; color:#fff !important; border:none !important;
  font-weight:600 !important; border-radius:11px !important; padding:10px 12px !important;
  box-shadow:0 6px 18px rgba(109,94,247,.28) !important;
  transition:background .13s ease, box-shadow .13s ease !important;
}
.st-key-new_chat button:hover{ background:var(--accent-2) !important;
  box-shadow:0 8px 22px rgba(109,94,247,.38) !important; }

/* History rows: text button (left) + small dim timestamp (right) */
section[data-testid="stSidebar"] .stButton>button{
  width:100%; justify-content:flex-start; text-align:left; background:transparent;
  color:var(--dim); border:1px solid transparent; border-radius:10px; padding:8px 10px;
  font-size:13.5px; font-weight:500; box-shadow:none !important;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  transition:background .13s ease, color .13s ease;
}
section[data-testid="stSidebar"] .stButton>button:hover{ background:rgba(255,255,255,.045); color:var(--text); }
section[data-testid="stSidebar"] .stButton>button[kind="primary"],
section[data-testid="stSidebar"] .stButton>button[data-testid="stBaseButton-primary"]{
  background:var(--accent-soft); color:var(--text); border-color:var(--accent-bd); font-weight:600;
}
.conv-row{ display:flex; align-items:center; gap:2px; }
.conv-time{ font-size:11px; color:var(--faint); white-space:nowrap; padding-right:4px; }

section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"]{
  height:calc(100vh - 266px) !important; min-height:160px; border:none !important;
  background:transparent !important; box-shadow:none !important; border-radius:0 !important;
  animation:none !important;
}

section[data-testid="stSidebar"] [data-testid="stPopover"]{ margin-top:10px; border-top:1px solid var(--bd); padding-top:10px; }
section[data-testid="stSidebar"] [data-testid="stPopover"] button{
  width:100%; justify-content:flex-start; background:transparent; color:var(--dim);
  border:1px solid transparent; border-radius:10px; font-weight:500; box-shadow:none !important;
}
section[data-testid="stSidebar"] [data-testid="stPopover"] button:hover{ background:rgba(255,255,255,.045); color:var(--text); }

/* ── Top bar ─────────────────────────────────────────────────────── */
.topbar{ display:flex; align-items:center; justify-content:space-between;
  padding:20px 4px 16px; }
.topbar-title{ font-size:16.5px; font-weight:600; color:var(--text); display:flex;
  align-items:center; gap:6px; }
.topbar-title .chev{ color:var(--faint); font-size:12px; }
.topbar-actions{ display:flex; gap:8px; }
.tb-btn button{ background:transparent !important; color:var(--dim) !important;
  border:1px solid var(--bd) !important; border-radius:10px !important; font-weight:500 !important;
  box-shadow:none !important; padding:6px 14px !important; font-size:13px !important;
}
.tb-btn button:hover{ color:var(--text) !important; border-color:var(--bd2) !important;
  background:rgba(255,255,255,.03) !important; }
.tb-icon button{ background:transparent !important; color:var(--dim) !important;
  border:1px solid var(--bd) !important; border-radius:10px !important; box-shadow:none !important;
  padding:6px 10px !important; }
.tb-icon button:hover{ color:var(--text) !important; border-color:var(--bd2) !important; }

/* ── Chat messages ───────────────────────────────────────────────── */
[data-testid="stChatMessage"]{ background:transparent !important; border:none !important;
  box-shadow:none !important; padding:7px 0 !important; gap:12px !important;
  animation:sqrise .3s ease both; }
[data-testid="stChatMessage"] p{ line-height:1.65; }

/* Avatars: small rounded violet tiles (assistant = sparkle, user = person) */
[data-testid^="stChatMessageAvatar"]{ background:var(--accent-soft) !important;
  color:var(--accent-2) !important; width:32px !important; height:32px !important;
  min-width:32px !important; border-radius:10px !important; border:1px solid var(--accent-bd);
  display:flex !important; align-items:center; justify-content:center; font-size:15px; margin-top:2px; }
[data-testid^="stChatMessageAvatar"] svg{ fill:var(--accent-2) !important; color:var(--accent-2) !important; }

/* User text -> full-width subtle card (left-aligned, like the screenshot) */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) > div:last-child{
  background:var(--card); border:1px solid var(--bd); border-radius:12px;
  padding:12px 16px; color:var(--text);
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) > div:last-child p{ margin:0; font-weight:500; }

/* ── Cards ───────────────────────────────────────────────────────── */
.sq-card{ background:var(--card); border:1px solid var(--bd); border-radius:var(--radius);
  padding:16px 18px; box-shadow:var(--shadow); animation:sqrise .32s ease both; }
.sq-row{ display:flex; align-items:center; justify-content:space-between; gap:14px; }
.sq-left{ display:flex; align-items:center; gap:12px; min-width:0; }
.sq-ico{ width:34px; height:34px; border-radius:10px; display:flex; align-items:center;
  justify-content:center; flex-shrink:0; }
.sq-ico.violet{ background:var(--accent-soft); color:var(--accent-2); }
.sq-ico.green{ background:var(--good-soft); color:var(--good); }
.sq-t1{ font-size:14.5px; font-weight:600; color:var(--text); }
.sq-t2{ font-size:13px; color:var(--dim); margin-top:2px; }
.sq-badge{ font-size:11.5px; font-weight:600; color:var(--good); background:var(--good-soft);
  border:1px solid var(--good-bd); border-radius:999px; padding:3px 10px; white-space:nowrap; }
.sq-badge.violet{ color:var(--accent-2); background:var(--accent-soft); border-color:var(--accent-bd); }
.sq-viewbtn button{ background:transparent !important; color:var(--dim) !important;
  border:1px solid var(--bd) !important; border-radius:9px !important; font-size:12.5px !important;
  font-weight:500 !important; box-shadow:none !important; padding:5px 12px !important; }
.sq-viewbtn button:hover{ color:var(--text) !important; border-color:var(--accent-bd) !important; }

.sq-bar-track{ height:6px; border-radius:99px; background:rgba(255,255,255,.06);
  overflow:hidden; margin-top:12px; }
.sq-bar-fill{ height:100%; border-radius:99px;
  background:linear-gradient(90deg,var(--accent),var(--accent-2));
  animation:sqindeterminate 1.4s ease-in-out infinite; }
@keyframes sqindeterminate{ 0%{width:12%; margin-left:0%;} 50%{width:55%; margin-left:30%;}
  100%{width:12%; margin-left:88%;} }

/* Bordered containers render as cards (so real Streamlit buttons can live
   INSIDE the card header, like the screenshot). The sidebar's own height
   container is reset back to plain (see the sidebar rule below). */
[data-testid="stVerticalBlockBorderWrapper"]{
  background:var(--card) !important; border:1px solid var(--bd) !important;
  border-radius:var(--radius) !important; box-shadow:var(--shadow);
  animation:sqrise .32s ease both; }
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlockBorderWrapper"]{
  box-shadow:none; animation:none; }
/* Buttons that live INSIDE a card (Edit / Copy / View summary / Done) */
[data-testid="stVerticalBlockBorderWrapper"] .stButton>button{
  background:transparent !important; color:var(--dim) !important;
  border:1px solid var(--bd) !important; border-radius:9px !important; font-size:12.5px !important;
  font-weight:500 !important; box-shadow:none !important; padding:5px 12px !important; }
[data-testid="stVerticalBlockBorderWrapper"] .stButton>button:hover{
  color:var(--text) !important; border-color:var(--accent-bd) !important; }
/* Card header title (icon + label) rendered as HTML inside the card */
.cardhead{ display:flex; align-items:center; gap:10px; }
.cardhead .sq-ico{ width:30px; height:30px; }

/* Email = editor-style card */
.sq-email{ padding:0; overflow:hidden; }
.sq-ebar{ display:flex; align-items:center; justify-content:space-between;
  padding:12px 16px; border-bottom:1px solid var(--bd); background:rgba(255,255,255,.02); }
.sq-ebar .sq-left{ gap:9px; }
.sq-etitle{ font-size:13.5px; font-weight:600; color:var(--text); }
.sq-ebtns{ display:flex; gap:6px; }
.sq-ebtns .stButton>button{ background:transparent !important; color:var(--dim) !important;
  border:1px solid var(--bd) !important; border-radius:9px !important; font-size:12.5px !important;
  font-weight:500 !important; box-shadow:none !important; padding:5px 12px !important; }
.sq-ebtns .stButton>button:hover{ color:var(--text) !important; border-color:var(--bd2) !important; }
.sq-esubj-l{ font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:var(--faint);
  font-weight:600; padding:12px 0 2px; }
.sq-esubj{ font-size:15.5px; font-weight:600; color:var(--text); padding:0 0 10px; }
.sq-ebody{ padding:4px 0 2px; color:#d3d7dd; font-size:14.5px; line-height:1.75; white-space:pre-wrap; }

.sq-notice{ background:var(--card); border:1px solid var(--bd); border-left:3px solid #d98a4b;
  border-radius:12px; padding:12px 16px; color:var(--dim); font-size:14px; }

.sq-dots{ display:inline-flex; gap:4px; margin-left:2px; }
.sq-dots span{ width:5px; height:5px; border-radius:50%; background:var(--accent-2); opacity:.4;
  animation:sqpulse 1.1s infinite ease-in-out; }
.sq-dots span:nth-child(2){ animation-delay:.18s; } .sq-dots span:nth-child(3){ animation-delay:.36s; }
@keyframes sqpulse{ 0%,100%{opacity:.25;transform:translateY(0);} 40%{opacity:1;transform:translateY(-2px);} }

/* ── Right panel ─────────────────────────────────────────────────── */
.rp-card{ background:var(--card); border:1px solid var(--bd); border-radius:var(--radius);
  padding:18px 19px; box-shadow:var(--shadow); animation:sqrise .34s ease both; margin-bottom:16px; }
.rp-head{ display:flex; align-items:center; gap:10px; margin-bottom:10px; }
.rp-title{ font-size:14.5px; font-weight:600; color:var(--text); }
.rp-desc{ font-size:13.5px; color:var(--dim); line-height:1.6; }
.rp-sub{ font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--faint);
  font-weight:700; margin:16px 0 8px; }
.rp-points{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:9px; }
.rp-points li{ position:relative; padding-left:16px; color:var(--dim); font-size:13px; line-height:1.55; }
.rp-points li:before{ content:""; position:absolute; left:1px; top:7px; width:5px; height:5px;
  border-radius:50%; background:var(--accent-2); }
.rp-sources{ display:flex; flex-wrap:wrap; gap:7px; }
.rp-src{ width:30px; height:30px; border-radius:9px; background:rgba(255,255,255,.04);
  border:1px solid var(--bd); display:flex; align-items:center; justify-content:center;
  font-size:11px; font-weight:700; color:var(--dim); text-decoration:none; transition:all .12s ease; }
.rp-src:hover{ border-color:var(--accent-bd); color:var(--accent-2); background:var(--accent-soft); }
.rp-src.more{ color:var(--faint); font-weight:600; }
.rp-contact{ display:flex; align-items:center; gap:12px; }
.rp-avatar{ width:38px; height:38px; border-radius:50%; background:var(--accent-soft);
  color:var(--accent-2); font-size:13px; font-weight:600; display:flex; align-items:center;
  justify-content:center; flex-shrink:0; }
.rp-cname{ font-size:14px; font-weight:600; color:var(--text); }
.rp-crole{ font-size:12.5px; color:var(--dim); margin-top:1px; }
.rp-in{ width:30px; height:30px; border-radius:9px; background:rgba(255,255,255,.04);
  border:1px solid var(--bd); display:flex; align-items:center; justify-content:center;
  font-size:12px; font-weight:700; color:var(--dim); text-decoration:none; flex-shrink:0;
  transition:all .12s ease; }
.rp-in:hover{ border-color:var(--accent-bd); color:var(--accent-2); background:var(--accent-soft); }
.rp-empty{ color:var(--faint); font-size:13px; text-align:center; padding:26px 10px; }

[data-testid="stExpander"]{ border:1px solid var(--bd) !important; border-radius:12px !important;
  background:var(--card) !important; margin-top:8px; }
[data-testid="stExpander"] summary{ color:var(--dim) !important; font-size:13px !important; }
[data-testid="stExpander"] summary:hover{ color:var(--text) !important; }

/* Empty-state hero */
.hero{ text-align:center; padding:16vh 10px 0; animation:sqrise .45s ease both; }
.hero-eyebrow{ font-size:11px; letter-spacing:.15em; text-transform:uppercase; color:var(--accent-2);
  font-weight:600; margin-bottom:14px; }
.hero-title{ font-size:42px; font-weight:700; letter-spacing:-.03em; color:var(--text); }
.hero-sub{ color:var(--dim); font-size:15px; line-height:1.6; max-width:440px; margin:14px auto 0; }
.hero-hints{ display:flex; gap:8px; justify-content:center; margin-top:22px; flex-wrap:wrap; }
.hero-hints span{ font-size:13px; color:var(--dim); background:var(--card); border:1px solid var(--bd);
  border-radius:999px; padding:6px 14px; }

/* ── Chat input ──────────────────────────────────────────────────── */
[data-testid="stBottomBlockContainer"]{ background:transparent !important; max-width:700px;
  margin-left:332px; padding-bottom:8px; }
.composer-tools{ max-width:700px; margin:0 0 6px 332px; display:flex; gap:8px; }
.composer-tools .stButton>button, .composer-tools [data-testid="stPopover"] button{
  background:var(--card) !important; color:var(--dim) !important; border:1px solid var(--bd) !important;
  border-radius:11px !important; font-size:12.5px !important; font-weight:500 !important;
  box-shadow:none !important; padding:7px 13px !important;
}
.composer-tools .stButton>button:hover, .composer-tools [data-testid="stPopover"] button:hover{
  color:var(--text) !important; border-color:var(--bd2) !important; }
[data-testid="stChatInput"]{ background:var(--card) !important; border:1px solid var(--bd2) !important;
  border-radius:22px !important; box-shadow:var(--shadow); transition:border-color .15s ease; }
[data-testid="stChatInput"]:focus-within{ border-color:var(--accent-bd) !important;
  box-shadow:var(--shadow), 0 0 0 3px rgba(109,94,247,.10) !important; }
[data-testid="stChatInput"] textarea{ color:var(--text) !important; font-size:15px !important; }
[data-testid="stChatInput"] textarea::placeholder{ color:var(--faint) !important; }
[data-testid="stChatInput"] button{ color:var(--accent-2) !important; }
.composer-hint{ text-align:center; color:var(--faint); font-size:11.5px; margin-top:8px; }

::-webkit-scrollbar{ width:9px; height:9px; }
::-webkit-scrollbar-thumb{ background:rgba(255,255,255,.09); border-radius:8px; }
::-webkit-scrollbar-thumb:hover{ background:rgba(255,255,255,.16); }
::-webkit-scrollbar-track{ background:transparent; }
</style>
"""


def _inject_theme() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def _esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def _relative_time(ts) -> str:
    """'2m ago' / '3h ago' / '5d ago' — no backend involved, pure display math."""
    try:
        delta = max(0, time.time() - float(ts))
    except (TypeError, ValueError):
        return ""
    if delta < 60:
        return "now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _source_mark(domain: str) -> str:
    """A short, deterministic 1-2 letter mark for a source chip (no icon fonts)."""
    if "linkedin" in domain:
        return "in"
    if "crunchbase" in domain:
        return "cb"
    core = domain.split(".")[0] if domain else "?"
    return (core[:2] or "?").upper()


# ── Session wiring (unchanged backend) ─────────────────────────────────
def _store() -> ConversationStore:
    if "store" not in st.session_state:
        st.session_state.store = ConversationStore()
    return st.session_state.store


def _active() -> Conversation:
    if "conv" not in st.session_state:
        st.session_state.conv = Conversation()
    return st.session_state.conv


def _select(conversation: Conversation) -> None:
    st.session_state.conv = conversation


# ── Card renderers ──────────────────────────────────────────────────────
def _render_email(data: dict, msg_key: str) -> None:
    subject_raw = data.get("subject") or ""
    body_raw = data.get("body") or ""
    edit_key = f"edit_{msg_key}"
    editing = st.session_state.get(edit_key, False)

    with st.container(border=True):
        head, spacer, b1, b2 = st.columns([2.4, 2.0, 1.0, 1.0],
                                          vertical_alignment="center")
        with head:
            st.markdown('<div class="cardhead"><div class="sq-ico violet">✉</div>'
                        '<div class="sq-t1">Draft Email</div></div>',
                        unsafe_allow_html=True)
        with b1:
            if st.button("✎ Edit", key=f"editbtn_{msg_key}", use_container_width=True):
                st.session_state[edit_key] = not editing
                st.rerun()
        with b2:
            _copy_button(f"Subject: {subject_raw}\n\n{body_raw}", key=msg_key)

        if editing:
            st.text_input("Subject", value=subject_raw, key=f"subj_{msg_key}",
                          label_visibility="collapsed")
            st.text_area("Body", value=body_raw, height=220, key=f"body_{msg_key}",
                         label_visibility="collapsed")
            if st.button("Done", key=f"done_{msg_key}"):
                st.session_state[edit_key] = False
                st.rerun()
        else:
            st.markdown(
                f'<div class="sq-esubj-l">Subject</div>'
                f'<div class="sq-esubj">{_esc(subject_raw)}</div>'
                f'<div class="sq-ebody">{_esc(body_raw).replace(chr(10), "<br>")}</div>',
                unsafe_allow_html=True)


def _copy_button(text: str, key: str) -> None:
    payload = json.dumps(text).replace("</", "<\\/")
    components.html(
        f"""
        <style>body{{margin:0;background:transparent;}}
        .cp{{font-family:Inter,system-ui,sans-serif;font-size:12.5px;font-weight:500;color:#9a9fa8;
          background:transparent;border:1px solid rgba(255,255,255,.13);border-radius:9px;
          padding:6px 13px;cursor:pointer;transition:all .12s ease;width:100%;}}
        .cp:hover{{color:#8677ff;border-color:rgba(109,94,247,.4);background:rgba(109,94,247,.08);}}</style>
        <button class="cp" id="cp{key}">Copy</button>
        <script>
          const b=document.getElementById("cp{key}");
          b.addEventListener("click",()=>{{navigator.clipboard.writeText({payload}).then(()=>{{
            b.textContent="Copied";setTimeout(()=>b.textContent="Copy",1600);}});}});
        </script>
        """,
        height=40,
    )


def _render_research(data: dict, msg_key: str) -> None:
    pages_n = len(data.get("pages_crawled") or [])
    score = data.get("research_score")
    score_txt = f"{int(score)}% confidence" if isinstance(score, (int, float)) else ""
    stat = " · ".join(p for p in
                      (f"Researched {pages_n} page{'s' if pages_n != 1 else ''}", score_txt) if p)

    what = data.get("what_they_do")
    hooks = [h for h in (data.get("hooks") or []) if h][:5]
    has_detail = bool(what or hooks or data.get("stop_reason"))
    see_key = f"see_{msg_key}"

    with st.container(border=True):
        c1, c2 = st.columns([3, 1.1], vertical_alignment="center")
        with c1:
            st.markdown(
                f'<div class="sq-left"><div class="sq-ico green">✓</div>'
                f'<div><div class="sq-t1">Research complete</div>'
                f'<div class="sq-t2">{_esc(stat)}</div></div></div>',
                unsafe_allow_html=True)
        with c2:
            if has_detail and st.button("View summary  ›", key=f"view_{msg_key}",
                                        use_container_width=True):
                st.session_state[see_key] = not st.session_state.get(see_key, False)

        if has_detail and st.session_state.get(see_key):
            st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)
            if what:
                st.markdown(f"**{_esc(data.get('company') or 'This company')}** — {_esc(what)}")
            if hooks:
                st.caption("Personalization angles")
                for h in hooks:
                    st.markdown(f"- {_esc(h)}")
            if data.get("stop_reason"):
                st.caption("Stopped: " + data["stop_reason"])


def _render_progress(label: str) -> None:
    st.markdown(
        f"""
        <div class="sq-card">
          <div class="sq-row">
            <div class="sq-left">
              <div class="sq-ico violet">⌕</div>
              <div><div class="sq-t1">{_esc(label)}</div>
                   <div class="sq-t2">Gathering information from their website
                   and online presence<span class="sq-dots"><span></span><span></span><span></span></span></div></div>
            </div>
            <span class="sq-badge violet">Researching</span>
          </div>
          <div class="sq-bar-track"><div class="sq-bar-fill"></div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_message(message, idx: int) -> None:
    # Rich cards render full-width with NO avatar (like the screenshot); only
    # text/notice messages carry an avatar (person for the user, sparkle for
    # the assistant).
    if message.kind == EMAIL:
        _render_email(message.data or {}, msg_key=f"m{idx}")
    elif message.kind == RESEARCH:
        _render_research(message.data or {}, msg_key=f"m{idx}")
    elif message.kind == NOTICE:
        with st.chat_message("assistant", avatar="✦"):
            st.markdown(f'<div class="sq-notice">{_esc(message.content)}</div>',
                        unsafe_allow_html=True)
    elif message.role == "user":
        with st.chat_message("user", avatar="👤"):
            st.markdown(message.content)
    else:
        with st.chat_message("assistant", avatar="✦"):
            st.markdown(message.content)


# ── Right panel: Research Summary / Key Points / Sources / Contact ─────
def _render_right_panel(conv: Conversation) -> None:
    research = conv.workspace.get("research")
    if not research or research.get("status") != "ok":
        st.markdown('<div class="rp-card"><div class="rp-empty">Research will '
                    'appear here once you ask about a company.</div></div>',
                    unsafe_allow_html=True)
        return

    data = research.get("data") or {}

    points = []
    if data.get("founder_name"):
        role = f" ({data['founder_role']})" if data.get("founder_role") else ""
        points.append(f"Founder: {data['founder_name']}{role}")
    if data.get("their_mission_or_why"):
        points.append(f"Mission: {data['their_mission_or_why']}")
    positioning = data.get("competitive_positioning") or data.get("product_category")
    if positioning:
        points.append(positioning)
    if data.get("notable_customers"):
        points.append("Customers: " + ", ".join(data["notable_customers"][:4]))
    if data.get("metrics_or_traction"):
        points.append(data["metrics_or_traction"])
    if data.get("tech_stack"):
        points.append("Tech: " + ", ".join(data["tech_stack"][:5]))
    if data.get("pricing_model"):
        points.append(f"Pricing: {data['pricing_model']}")
    points = points[:6]

    st.markdown('<div class="rp-card">', unsafe_allow_html=True)
    st.markdown(
        f"""<div class="rp-head"><div class="sq-ico violet" style="width:28px;height:28px;">▤</div>
        <div class="rp-title">Research Summary</div></div>
        <div class="rp-desc">{_esc(data.get('what_they_do') or '')}</div>""",
        unsafe_allow_html=True,
    )
    if points:
        items = "".join(f"<li>{_esc(p)}</li>" for p in points)
        st.markdown(f'<div class="rp-sub">Key points</div><ul class="rp-points">{items}</ul>',
                    unsafe_allow_html=True)

    pages = research.get("pages_crawled") or []
    domains = list(dict.fromkeys(_domain(u) for u in pages if _domain(u)))
    if domains:
        shown, extra = domains[:4], max(0, len(domains) - 4)
        chips = "".join(
            f'<a class="rp-src" href="{_esc(pages[i])}" target="_blank" '
            f'rel="noopener noreferrer" title="{_esc(domains[i])}">{_esc(_source_mark(domains[i]))}</a>'
            for i in range(len(shown))
        )
        if extra:
            chips += f'<span class="rp-src more">+{extra}</span>'
        st.markdown(f'<div class="rp-sub">Sources ({len(domains)})</div>'
                    f'<div class="rp-sources">{chips}</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    contact = data.get("primary_contact_name") or data.get("founder_name")
    if contact:
        role = data.get("primary_contact_role") or data.get("founder_role") or ""
        initials = "".join(w[0] for w in str(contact).split()[:2]).upper() or "•"
        company = data.get("company_name") or ""
        li = ("https://www.linkedin.com/search/results/people/?keywords="
              + quote(f"{contact} {company}".strip()))
        st.markdown(
            f"""<div class="rp-card">
              <div class="rp-head"><div class="sq-ico violet" style="width:28px;height:28px;">☺</div>
                <div class="rp-title">Contact</div></div>
              <div class="rp-contact">
                <div class="rp-avatar">{_esc(initials)}</div>
                <div style="flex:1;min-width:0;"><div class="rp-cname">{_esc(contact)}</div>
                     <div class="rp-crole">{_esc(role)}</div></div>
                <a class="rp-in" href="{_esc(li)}" target="_blank" rel="noopener noreferrer"
                   title="Find on LinkedIn">in</a>
              </div></div>""",
            unsafe_allow_html=True,
        )


# ── Sidebar ──────────────────────────────────────────────────────────────
def _sidebar() -> None:
    store, active = _store(), _active()
    with st.sidebar:
        st.markdown('<div class="brand">Saqua</div>', unsafe_allow_html=True)

        if st.button("＋  New Chat", use_container_width=True, key="new_chat"):
            _select(Conversation())
            st.rerun()

        st.markdown('<div class="side-label">Conversations</div>', unsafe_allow_html=True)
        summaries = store.list_summaries()
        with st.container(height=400):
            if not summaries:
                st.markdown('<div class="side-empty">No conversations yet.</div>',
                            unsafe_allow_html=True)
            for summary in summaries:
                is_active = summary["id"] == active.id
                row_l, row_r = st.columns([0.78, 0.22], vertical_alignment="center")
                with row_l:
                    if st.button("💬  " + (summary["title"] or "Conversation"),
                                key="conv_" + str(summary["id"]),
                                use_container_width=True,
                                type="primary" if is_active else "secondary"):
                        loaded = store.load(summary["id"])
                        if loaded:
                            _select(loaded)
                            st.rerun()
                with row_r:
                    st.markdown(f'<div class="conv-time">'
                               f'{_esc(_relative_time(summary.get("updated_at")))}</div>',
                               unsafe_allow_html=True)

        with st.popover("⚙  Settings", use_container_width=True):
            _settings_panel()


def _settings_panel() -> None:
    from config.settings import CLAUDE_MODEL  # read-only display

    search_on = bool(os.environ.get("TAVILY_API_KEY", "").strip()
                     or os.environ.get("BRAVE_API_KEY", "").strip())
    st.markdown("**Saqua**")
    st.caption("An AI teammate for company research and personalized outbound.")
    st.divider()
    st.markdown(f"- **Model** · `{CLAUDE_MODEL}`")
    st.markdown(f"- **Connection** · {'Connected' if _HAS_KEY else 'No API key'}")
    st.markdown(f"- **Company lookup** · "
                f"{'Search enabled' if search_on else 'Best-effort (no search key)'}")


# ── Top bar ──────────────────────────────────────────────────────────────
def _top_bar(conv: Conversation, store: ConversationStore) -> None:
    label = conv.workspace.get("company") or conv.title or "New chat"
    c1, c2, c3, c4 = st.columns([6, 1, 1, 1])
    with c1:
        st.markdown(f'<div class="topbar-title">{_esc(label)} '
                    f'<span class="chev">▾</span></div>', unsafe_allow_html=True)
    with c2:
        transcript = "\n\n".join(
            f"[{m.role}] {m.content}" for m in conv.messages if m.content)
        st.markdown('<div class="tb-icon">', unsafe_allow_html=True)
        st.download_button("⤓", data=transcript or "(empty conversation)",
                           file_name=f"{(label or 'saqua')}.md", mime="text/markdown",
                           key="export_btn", help="Export conversation")
        st.markdown("</div>", unsafe_allow_html=True)
    with c3:
        st.markdown('<div class="tb-btn">', unsafe_allow_html=True)
        if st.button("Share", key="share_btn", use_container_width=True):
            st.toast("Link sharing isn't available yet — it's on the roadmap.")
        st.markdown("</div>", unsafe_allow_html=True)
    with c4:
        st.markdown('<div class="tb-icon">', unsafe_allow_html=True)
        with st.popover("⋯", use_container_width=True):
            new_title = st.text_input("Rename conversation", value=conv.title,
                                      key="rename_input")
            if st.button("Save name", key="rename_save", use_container_width=True):
                conv.title = new_title.strip() or conv.title
                store.save(conv)
                st.rerun()
            st.divider()
            if st.button("Delete conversation", key="delete_conv", use_container_width=True):
                store.delete(conv.id)
                _select(Conversation())
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


# ── Main chat area ───────────────────────────────────────────────────────
def _render_hero() -> None:
    st.markdown(
        """
        <div class="hero">
          <div class="hero-eyebrow">AI outbound research</div>
          <div class="hero-title">Saqua</div>
          <div class="hero-sub">Research any company and draft a personalized cold
            email — then refine it just by chatting. Start with a company name or website.</div>
          <div class="hero-hints"><span>Stripe</span><span>notion.so</span>
            <span>Clay</span><span>Cursor</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _composer_toolbar() -> None:
    """Decorative-but-honest row above the input: attach (not built yet) and a
    real, read-only list of the agent's registered capabilities."""
    st.markdown('<div class="composer-tools">', unsafe_allow_html=True)
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("＋", key="attach_btn", help="Attach a file"):
            st.toast("Attachments aren't supported yet — it's on the roadmap.")
    with c2:
        with st.popover("⚏  Tools"):
            st.caption("What Saqua can do in this chat")
            for t in tools.REGISTRY.values():
                soon = "not available yet" in t.description.lower()
                mark = "◌" if soon else "●"
                st.markdown(f"{mark} **{t.name}** — {t.description.split('(Not available')[0].strip()}")
    st.markdown("</div>", unsafe_allow_html=True)


def _main() -> None:
    conv = _active()
    store = _store()

    if conv.is_empty():
        _render_hero()
    else:
        left, right = st.columns([2.7, 1], gap="large")
        with left:
            _top_bar(conv, store)
            for idx, message in enumerate(conv.messages):
                _render_message(message, idx)
        with right:
            _render_right_panel(conv)

    _composer_toolbar()
    placeholder = "Enter a company website or name…" if conv.is_empty() else "Ask anything…"
    prompt = st.chat_input(placeholder, disabled=not _HAS_KEY)
    st.markdown('<div class="composer-hint">Enter to send &nbsp;·&nbsp; '
                'Shift + Enter for a new line</div>', unsafe_allow_html=True)
    if not prompt:
        return

    if len(conv.messages) >= MAX_MESSAGES_PER_SESSION:
        st.warning("This conversation has reached its length limit. Start a new chat.")
        return

    # Echo the user's message and a progress card while the agent works. This
    # block only renders during the live (blocking) call; the persisted
    # RESEARCH/EMAIL cards render on the rerun that follows via _render_message.
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        if conv.is_empty():
            _render_progress(f"Researching {prompt.strip()}")
        else:
            st.markdown(f'<div class="sq-t2">Thinking'
                        '<span class="sq-dots"><span></span><span></span><span></span></span>'
                        "</div>", unsafe_allow_html=True)
        agent.respond(conv, prompt, store)
    st.rerun()


_inject_theme()
_sidebar()
_main()
