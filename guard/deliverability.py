"""Part 2 — Deliverability Guard.

Inspects an outgoing email (and its sequence/mailbox context) for everything that
hurts inbox placement or sender reputation: spam triggers, robotic AI phrasing,
weak/generic personalization, bad structure, unsafe sequence behaviour, and risky
mailbox stats. It BLOCKS the clear reputation-killers (sending after a reply /
unsubscribe / bounce, duplicate recipients, spam-cannon copy) and WARNS on the
rest. It never rewrites the email — it only scores and recommends.

Reuses the writer's spam/reading heuristics and the shared banned-phrase list so
there is one source of truth for "what sounds like AISpam".
"""

import difflib
import re

from agents import ai_voice
from agents.writer_prompt import BANNED_MATCH, stem_present
from agents.writer_review import _SPAMMY, _spam_risk
from guard.models import Findings, as_bool, as_float, as_int, get

# The spec's high-risk phrases (deliverability + "sounds like a template" tells).
_HIGH_RISK_PHRASES = (
    "just checking", "following up", "circle back", "circling back",
    "hope you're well", "hope you are well", "hope this finds you",
    "quick question", "reaching out", "touching base", "touch base",
    "limited time", "act now", "guaranteed", "guarantee", "risk free",
    "risk-free", "free offer", "click here", "buy now", "no obligation",
    "100% free", "special promotion", "dear sir", "dear madam",
)
_URGENCY = ("act now", "limited time", "expires", "urgent", "last chance",
            "don't miss", "hurry", "today only", "final notice")
_BUZZWORDS = ("synergy", "leverage", "cutting-edge", "best-in-class", "seamless",
              "revolutionary", "game-changer", "game changer", "disrupt",
              "paradigm", "next-generation", "world-class", "turnkey",
              "value-add", "circle back", "move the needle", "low-hanging fruit")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")

# Reputation thresholds (fractions, e.g. 0.03 == 3%).
_BOUNCE_WARN, _BOUNCE_BLOCK = 0.03, 0.08
_SPAM_WARN, _SPAM_BLOCK = 0.001, 0.003
_REPLY_POOR = 0.01
_NEW_MAILBOX_DAYS = 30
_MIN_SEQUENCE_SPACING_DAYS = 2
_MIN_USEFUL_WORDS = 25
# Per-channel character ceilings (X hard-caps; the rest are "don't ramble").
_CHANNEL_CHAR_LIMITS = {
    "x_reply": 280, "reddit_comment": 1500, "hn_reply": 1000, "contact_form": 900,
}
_GENERIC_OUTBOUND_RE = re.compile(
    r"\b(?:i\s+help|we\s+help)\s+(?:founders|teams|companies)\s+"
    r"(?:get|land|generate|book|find|reach)",
    re.IGNORECASE,
)


def _count(value):
    """Accept either a count (int) or a list and return its length."""
    if isinstance(value, (list, tuple)):
        return len(value)
    return as_int(value) or 0


def _phrase_hits(text, phrases):
    low = text.lower()
    return [p for p in phrases if p in low]


def evaluate(inp: dict) -> Findings:
    f = Findings()
    email = get(inp, "email", default={}) or {}
    subject = str(get(email, "subject", default="") or "")
    body = str(get(email, "body", default="") or "")
    text = subject + "\n" + body
    words = body.split()
    n_words = len(words)
    has_email_artifact = isinstance(email, dict) and bool(email)
    # Channel awareness: the SAME AI-voice / banned-phrase / spam logic applies to
    # every channel, but email-only hard rules (a subject, a recipient, a 25-word
    # floor) don't fit a public reply or a contact-form message. Default is
    # "email", so an input with no channel behaves exactly as before.
    channel = str(get(email, "channel", default="")
                  or get(inp, "channel", default="") or "email").strip().lower()
    is_email = channel in ("", "email")

    # ── send-state safety (hard blocks) ──
    prospect = get(inp, "prospect", default={}) or {}
    if as_bool(get(prospect, "replied"), False):
        f.block_now("This prospect already replied — do not send another email.",
                    "Stop the sequence; a human should take the reply.")
    if as_bool(get(prospect, "unsubscribed"), False):
        f.block_now("This prospect unsubscribed — sending would be a violation.",
                    "Suppress this address permanently.")
    if as_bool(get(prospect, "bounced"), False):
        f.block_now("This address previously bounced — sending harms reputation.",
                    "Remove the bounced address from the list.")

    # ── duplicate recipients (hard block) ──
    recips = get(inp, "recipients", default=None)
    if isinstance(recips, (list, tuple)):
        norm = [str(r).strip().lower() for r in recips if str(r).strip()]
        if len(norm) != len(set(norm)):
            f.block_now("Duplicate recipients in this batch.",
                        "De-duplicate the recipient list before sending.")
    elif as_int(get(inp, "recipients", "duplicates")):
        f.block_now("Duplicate recipients detected.",
                    "De-duplicate the recipient list before sending.")

    _pipeline_blocks(inp, f)

    if has_email_artifact and is_email and not subject.strip():
        f.block_now("Empty email subject.", "Generate a real subject before sending.")
    if has_email_artifact and not body.strip():
        f.block_now(f"Empty {channel} message.", "Add real content before posting.")
        return f
    if has_email_artifact and is_email:
        if not str(get(email, "to", default="") or get(inp, "recipient", default="")).strip():
            f.block_now("Missing recipient.", "Add a verified recipient before sending.")
        if not _company_present(inp):
            f.block_now("Missing recipient company.",
                        "Attach the company/research context before sending.")

    writer_status = str(get(inp, "writer", "status", default="") or "").lower()
    if writer_status in ("error", "skip"):
        f.block_now("Writer did not produce a sendable draft.",
                    "Fix the writer/research issue before attempting to send.")

    # Channel length ceiling (X caps replies; the others just shouldn't ramble).
    limit = _CHANNEL_CHAR_LIMITS.get(channel)
    if limit and len(body) > limit:
        f.add(18, f"Too long for {channel} ({len(body)}/{limit} chars).",
              f"Trim the {channel} message to fit its limit.")

    if not body.strip():
        return f

    # ── spam signals ──
    spam = _spam_risk(subject, body)
    if spam == "high":
        f.add(28, "High spam-filter risk in the copy.",
              "Remove salesy/urgency phrasing, links, and shouty formatting.")
    elif spam == "medium":
        f.add(12, "Moderate spam-filter risk in the copy.")
    spammy = _phrase_hits(text, _SPAMMY)
    if spammy:
        f.add(6 * min(3, len(spammy)),
              "Spam trigger phrases: " + ", ".join(spammy[:5]) + ".",
              "Cut spam-trigger words like 'free', 'guarantee', 'act now'.")

    high_risk = _phrase_hits(text, _HIGH_RISK_PHRASES)
    if len(high_risk) >= 3:
        f.add(20, "Overused cold-email clichés: " + ", ".join(high_risk[:5]) + ".",
              "Replace tired openers ('just checking', 'circle back', 'hope you're well').")
    elif high_risk:
        f.add(8, "Cold-email cliché(s): " + ", ".join(high_risk[:3]) + ".")

    if _phrase_hits(text, _URGENCY):
        f.add(10, "Urgency/manipulation language.",
              "Drop false urgency — it reads as spam and erodes trust.")
    buzz = _phrase_hits(text, _BUZZWORDS)
    if len(buzz) >= 2:
        f.add(10, "Buzzword overload: " + ", ".join(buzz[:5]) + ".",
              "Say it plainly; buzzwords read as AI/marketing filler.")

    # ── AI / template tells (wording AND sentence structure) ──
    # Two independent signals, one shared source of truth (agents.ai_voice): the
    # banned-PHRASE list (vocabulary) and the STRUCTURAL scan (tricolon, "not X
    # but Y", participial tails, metronome rhythm). Either alone raises risk;
    # stacked wording, or wording + robotic structure together, is a generated
    # blast and is BLOCKED — the "no AI voice" rule enforced in code, not just the
    # writer's prompt (which a pasted or hand-edited draft never passed through).
    ai_hits = [readable for readable, stem in BANNED_MATCH
               if stem_present(stem, text.lower())]
    struct = ai_voice.scan(body)
    struct_labels = [label for label, _ in struct]
    struct_penalty = min(40, sum(w for _, w in struct))

    if len(ai_hits) >= 3:
        f.block_now(
            "Reads machine-generated — stacked AI/template phrases ("
            + ", ".join(ai_hits[:4]) + ").",
            "Rewrite it in a real human voice; copy this templated won't get replies.")
    elif len(ai_hits) >= 2:
        f.add(16, "Reads AI-generated (" + ", ".join(ai_hits[:4]) + ").",
              "Make it sound like a person typed it once, not a template.")
    elif ai_hits:
        f.add(6, "A phrase that reads AI-generated: " + ai_hits[0] + ".")

    if struct:
        f.add(struct_penalty,
              "AI sentence structure: " + "; ".join(struct_labels[:3]) + ".",
              "Vary sentence length; drop the rule-of-three and 'not X but Y', "
              "and cut the '..., ensuring ...' clause tails.")
        # Templated WORDING plus robotic STRUCTURE = a generated blast. Block.
        if ai_hits and struct_penalty >= 20:
            f.block_now(
                "Reads like a generated template (AI phrasing plus AI sentence "
                "structure).",
                "Don't send as-is — rewrite it so a person clearly wrote it.")

    # ── formatting / shouting ──
    caps = [w for w in re.findall(r"[A-Za-z]{3,}", text) if w.isupper()]
    if len(caps) >= 3:
        f.add(12, "Excessive ALL-CAPS.", "Use normal case; ALL-CAPS trips spam filters.")
    if re.search(r"[!?]{2,}", text) or text.count("!") >= 3:
        f.add(8, "Excessive punctuation (!!! / ???).", "Use at most one exclamation.")
    emoji = len(_EMOJI.findall(text))
    if emoji >= 3:
        f.add(8, f"Heavy emoji use ({emoji}).", "Cut emojis in cold B2B email.")
    elif emoji:
        f.add(3, "Emoji in a cold email.")

    # ── links / images / attachments ──
    links = _count(get(email, "links"))
    if links >= 4:
        f.add(16, f"Too many links ({links}).", "Keep to at most one link in a cold email.")
    elif links >= 2:
        f.add(7, f"{links} links — more than one hurts deliverability.")
    images = _count(get(email, "images"))
    if images >= 3:
        f.add(10, f"Many images ({images}).", "Cold emails should be mostly plain text.")
    attachments = _count(get(email, "attachments"))
    if attachments:
        f.add(14, f"{attachments} attachment(s) on a cold email.",
              "Never attach files to a first cold email — it screams spam.")

    # ── html / text ratio ──
    html = get(email, "html")
    if html or as_bool(get(email, "is_html"), False):
        html_str = str(html or "")
        tag_chars = len("".join(re.findall(r"<[^>]+>", html_str)))
        if html_str and tag_chars > len(html_str) * 0.5:
            f.add(8, "Heavy HTML relative to text.",
                  "Prefer plain text; a low text-to-HTML ratio looks like spam.")

    # ── length / readability ──
    # The 25-word floor is an EMAIL rule (a one-line cold email reads like spam);
    # a public reply or contact-form note is legitimately short, so only block on
    # near-empty there.
    if is_email and n_words < _MIN_USEFUL_WORDS:
        f.block_now(f"Body is below the minimum useful length ({n_words} words).",
                    "Write a complete, specific email before sending.")
    elif not is_email and n_words < 3:
        f.block_now("Message is essentially empty.", "Write a real message before posting.")
    elif n_words > 200:
        f.add(10, f"Long body ({n_words} words); cold emails should be short.",
              "Cut it under ~120 words.")
    # Reading level (proxy): dense, long sentences lower comprehension + replies.
    sentences = [s for s in re.split(r"[.!?]+", body) if s.strip()]
    if sentences and n_words / len(sentences) > 25:
        f.add(6, f"Dense sentences (~{n_words / len(sentences):.0f} words each) — hard to skim.",
              "Use shorter sentences; long ones read as effortful and lower replies.")

    # ── personalization ──
    _personalization(inp, body, f)

    # ── template repetition vs prior emails ──
    _repetition(inp, body, f)

    # ── sequence safety ──
    _sequence(inp, f)

    # ── mailbox reputation + volume ──
    _mailbox(inp, f)

    # ── authentication ──
    _auth(inp, f)

    return f


def _personalization(inp, body, f):
    p = get(inp, "personalization", default=None)
    if _GENERIC_OUTBOUND_RE.search(body or "") and not _company_mentioned(inp, body):
        f.block_now("Generic founder-outbound pitch with no company connection.",
                    "Tie the email to a specific, verified detail about this company.")
    if p is None:
        return
    if isinstance(p, dict):
        if as_bool(get(p, "fabricated"), False):
            f.block_now("Personalization appears fabricated.",
                        "Never send invented facts — use only real research.")
            return
        generic = as_bool(get(p, "generic"), None)
        based = as_bool(get(p, "based_on_research"), None)
        specific = as_bool(get(p, "specific"), None)
        if generic is True or specific is False:
            f.block_now("Generic personalization.",
                        "Reference a specific, true detail about the prospect.")
        if based is False:
            f.block_now("Personalization not grounded in research.",
                        "Base personalization on real, verified research.")
    elif isinstance(p, (list, tuple)):
        items = [x for x in p if isinstance(x, dict)]
        if not items:
            return
        if any(as_bool(x.get("fabricated"), False) for x in items):
            f.block_now("A personalization item appears fabricated.",
                        "Remove invented details; use only real research.")
        weak = sum(1 for x in items if x.get("specific") is False
                   or x.get("source") in (None, "", "generic"))
        if weak and weak >= len(items) / 2:
            f.block_now("Most personalization is generic or unsourced.",
                        "Ground each detail in specific, real research.")


def _pipeline_blocks(inp, f):
    qrec = str(get(inp, "qualification", "recommendation", default="") or "").lower()
    if qrec in ("reject", "research_more"):
        f.block_now(f"Qualification is '{qrec}', so this lead should not be sent.",
                    "Do not write/send until the lead is qualified to continue.")
    action = str(get(inp, "strategy", "recommended_action", default="") or "").lower()
    if action in ("hold", "research", "enrich"):
        f.block_now(f"Strategy action is '{action}', not send-ready.",
                    "Resolve the strategy hold before sending.")


def _company_present(inp) -> bool:
    return bool(
        str(get(inp, "company", default="") or "").strip()
        or str(get(inp, "prospect", "company", default="") or "").strip()
        or str(get(inp, "research", "company_name", default="") or "").strip()
        or str(get(inp, "email", "company", default="") or "").strip()
    )


def _company_mentioned(inp, body) -> bool:
    names = [
        get(inp, "company", default=""),
        get(inp, "prospect", "company", default=""),
        get(inp, "research", "company_name", default=""),
        get(inp, "email", "company", default=""),
    ]
    text = (body or "").lower()
    return any(str(name).strip().lower() in text for name in names if str(name).strip())


def _repetition(inp, body, f):
    priors = get(inp, "sequence", "prior_bodies", default=None) \
        or get(inp, "prior_bodies", default=None)
    if not isinstance(priors, (list, tuple)):
        return
    b = body.strip().lower()
    for prev in priors:
        ratio = difflib.SequenceMatcher(a=b, b=str(prev).strip().lower()).ratio()
        if ratio >= 0.85:
            f.add(20, f"Nearly identical to a previous email ({ratio*100:.0f}% match).",
                  "Vary the copy; repeated sends look like a template blast.")
            break
        if ratio >= 0.6:
            f.add(8, "Similar structure/wording to a previous email.")
            break


def _sequence(inp, f):
    seq = get(inp, "sequence", default={}) or {}
    spacing = get(seq, "spacing_days")
    spacings = spacing if isinstance(spacing, (list, tuple)) else (
        [spacing] if spacing is not None else [])
    for s in spacings:
        val = as_float(s)
        if val is not None and val <= 0:
            f.block_now("Sequence step has zero spacing — duplicate/instant send.",
                        "Space follow-ups at least a couple of days apart.")
        elif val is not None and val < _MIN_SEQUENCE_SPACING_DAYS:
            f.add(8, f"Follow-up spacing is tight ({val} day(s)).",
                  "Give follow-ups a few days of breathing room.")


def _mailbox(inp, f):
    mb = get(inp, "mailbox", default={}) or {}
    age = as_int(get(mb, "age_days"))
    sent_today = as_int(get(mb, "sent_today"))
    max_daily = as_int(get(mb, "max_daily"))
    volume = as_int(get(mb, "daily_volume")) or sent_today

    if age is not None and age < _NEW_MAILBOX_DAYS:
        f.add(14, f"New mailbox ({age} days old).",
              "Warm up slowly — keep daily volume low for a few weeks.")
    if max_daily and volume and volume > max_daily:
        f.add(16, f"Daily volume {volume} exceeds the mailbox limit {max_daily}.",
              "Throttle sends to stay under the mailbox's safe daily cap.")
    bounce = as_float(get(mb, "bounce_rate"))
    if bounce is not None:
        if bounce >= _BOUNCE_BLOCK:
            f.block_now(f"Bounce rate is dangerously high ({bounce*100:.1f}%).",
                        "Stop sending and clean the list; reputation is at risk.")
        elif bounce >= _BOUNCE_WARN:
            f.add(16, f"Elevated bounce rate ({bounce*100:.1f}%).",
                  "Verify addresses before sending more.")
    spam_rate = as_float(get(mb, "spam_rate"))
    if spam_rate is not None:
        if spam_rate >= _SPAM_BLOCK:
            f.block_now(f"Spam-complaint rate is critical ({spam_rate*100:.2f}%).",
                        "Pause sending immediately; you're being marked as spam.")
        elif spam_rate >= _SPAM_WARN:
            f.add(18, f"Rising spam complaints ({spam_rate*100:.2f}%).",
                  "Slow down and review targeting/copy.")
    reply = as_float(get(mb, "reply_rate"))
    if reply is not None and reply < _REPLY_POOR and (volume or 0) >= 20:
        f.add(10, f"Low reply rate ({reply*100:.1f}%) at meaningful volume.",
              "Improve targeting/personalization before scaling volume.")
    if as_bool(get(mb, "unsubscribe_supported"), True) is False:
        f.add(10, "No unsubscribe handling.",
              "Add an easy opt-out — required for compliant, deliverable sending.")


def _auth(inp, f):
    auth = get(inp, "auth", default=None)
    if not isinstance(auth, dict):
        return
    missing = [k.upper() for k in ("spf", "dkim", "dmarc")
               if as_bool(get(auth, k), None) is False]
    if missing:
        f.add(6 * len(missing), "Email authentication missing: " + ", ".join(missing) + ".",
              "Set up SPF, DKIM, and DMARC — without them mail lands in spam.")
