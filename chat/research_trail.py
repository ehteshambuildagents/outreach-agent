"""Canonical research-trail events — the structured, evidence-bearing record of
what the agent ACTUALLY did.

These are deliberately distinct from the ephemeral step/thought ticker:

  * a trail event is PERSISTED on the conversation, so a restored thread still
    shows the honest research trail (and its evidence links) instead of replaying
    a fake animation;
  * it is emitted only at a REAL execution boundary (a tool starting, completing,
    or failing) — nothing here is a scripted "AI talking to itself";
  * a ``provider`` is only ever the tool that truly ran, and every ``source`` URL
    is validated before it is stored, so a link can never carry an unsafe scheme
    (``javascript:``/``data:``) or a fabricated origin into the client.

One event schema, shared by the API, the persisted thread, and the tests, so the
live stream and the restored trail are byte-for-byte the same shape.
"""

import time
import uuid

# Canonical statuses (the brief's set).
PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
SKIPPED = "skipped"

MAX_TRAIL = 60             # cap persisted events per conversation (newest kept)


def new_run_id() -> str:
    """A per-turn run id, so events from one turn group together and a restored
    trail can show turns as distinct runs."""
    return "run_" + uuid.uuid4().hex[:16]


def safe_url(url):
    """Return a display-safe absolute URL (http/https, real host), else None.

    Rejects ``javascript:``/``data:``/relative/hostless URLs and anything with
    embedded whitespace or control characters, so a stored source can never carry
    an unsafe scheme or an obfuscated one into the UI. The frontend validates again
    (defence in depth), but nothing unsafe is ever persisted in the first place."""
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    if not u or " " in u or any(ord(c) < 0x20 for c in u):
        return None
    lower = u.lower()
    if not (lower.startswith("http://") or lower.startswith("https://")):
        return None
    host = u.split("://", 1)[1].split("/", 1)[0].split("@")[-1].split(":")[0]
    if not host or "." not in host:
        return None
    return u


def domain_of(url):
    safe = safe_url(url)
    if not safe:
        return None
    host = safe.split("://", 1)[1].split("/", 1)[0].split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def source(title=None, url=None, *, official=False):
    """A validated evidence source ({title, url, domain, official}), or None when
    the URL is unusable. ``official`` marks a first-party source (the company's own
    site) so the UI can distinguish it from third-party corroboration."""
    safe = safe_url(url)
    if not safe:
        return None
    dom = domain_of(safe)
    return {"title": (str(title).strip() if title else (dom or safe))[:200],
            "url": safe, "domain": dom, "official": bool(official)}


def dedupe_sources(sources):
    """Drop falsy and duplicate (same-URL) sources, preserving order."""
    out, seen = [], set()
    for s in sources or []:
        if not s or not s.get("url"):
            continue
        key = s["url"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def event(*, run_id, event_type, label, status=RUNNING, target=None, detail=None,
          provider=None, sources=None, confidence=None):
    """Build one canonical trail event.

    Pass a ``provider`` ONLY when that tool/provider was really called, and
    ``sources`` only for links that were really used — this record is meant to be
    trusted enough to click. Sources are validated + de-duplicated here."""
    return {
        "event_id": "ev_" + uuid.uuid4().hex[:16],
        "run_id": run_id,
        "event_type": str(event_type),
        "label": str(label),
        "status": status,
        "target": target or None,
        "detail": (str(detail) if detail else None),
        "provider": provider or None,
        "sources": dedupe_sources(sources),
        "confidence": confidence,
        "ts": time.time(),
    }
