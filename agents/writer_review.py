"""Silent self-review for the email writer (deterministic, no model call).

Before returning a draft, the writer scores it on the qualities that separate a
real founder email from an AI one, and — if it is weak — feeds the specific
issues back into ONE bounded regeneration (reusing the existing repair loop, so
the happy path stays a single Claude call). The score is internal: it never
leaves the writer and is never shown to the user unless they explicitly ask.

This complements ``writer_validator`` rather than replacing it: the validator
enforces HARD rules (banned wording, length, greeting, placeholders) that BLOCK a
send; this review flags SOFT quality gaps (thin personalization, missing CTA,
robotic uniformity) that should be improved when possible but never block a
grounded draft from being returned.
"""

import re

from agents.writer_validator import _strip_ps

# Score below this (out of 100) => "weak" => try to improve within the repair
# budget. Deliberately lenient: we nudge obviously-weak drafts, we don't chase
# perfection at the cost of extra model calls.
REVIEW_WEAK_THRESHOLD = 62

# Contractions are a founder-voice tell; their absence reads stiff/AI.
_CONTRACTION_RE = re.compile(r"\b\w+['’](?:s|re|ve|ll|d|t|m)\b|\bn['’]t\b", re.IGNORECASE)
# Openers that read like generic cold-email throat-clearing.
_WEAK_OPENERS = (
    "i hope", "hope you", "hope this", "i wanted", "i just wanted",
    "my name is", "i am reaching", "i'm reaching", "i am writing to",
    "quick question for you", "i came across", "i noticed",
)
_PASSIVE_ENDINGS = (
    "i'll leave it", "leave it with you", "you'll know if", "figured i'd flag",
    "drop it here", "thinking out loud", "anyway,",
)
_VAGUE_CTAS = (
    "worth exploring", "worth a look", "thoughts?", "make sense?",
    "something you feel", "bumping into right now",
)
_GENERIC_FOUNDER_OUTBOUND = (
    "help founders get replies", "founders actual replies",
    "personalized cold email", "hours per prospect", "without spending hours",
    "personalization grind", "generic templates",
)


class Review:
    """Result of a self-review: a score, whether it's weak, and the issues to
    feed a regeneration. `issues` is empty when the draft is strong."""

    __slots__ = ("score", "issues", "dimensions")

    def __init__(self, score, issues, dimensions):
        self.score = score
        self.issues = issues
        self.dimensions = dimensions

    @property
    def weak(self) -> bool:
        return self.score < REVIEW_WEAK_THRESHOLD


def _grounded_tokens(data: dict) -> set:
    """Lowercase word tokens from grounded facts the email could personalize on."""
    tokens = set()
    def add(v):
        for w in re.findall(r"[A-Za-z0-9]{3,}", str(v or "").lower()):
            tokens.add(w)
    for key in ("company_name", "unique_hook", "recent_focus",
                "metrics_or_traction", "their_mission_or_why", "product_category",
                "competitive_positioning", "target_customer"):
        add(data.get(key))
    for key in ("additional_hooks", "notable_customers", "industries_served",
                "product_differentiators", "tech_stack"):
        for item in data.get(key) or []:
            add(item)
    # Common words that shouldn't count as "personalization" on their own.
    return tokens - {"the", "and", "for", "with", "that", "you", "your", "our",
                     "who", "how", "what", "they", "their", "are", "was", "has"}


def review(draft: dict, data: dict) -> Review:
    """Score a draft 0-100 across founder-email qualities; list soft issues."""
    body = _strip_ps((draft or {}).get("body") or "").strip()
    subject = ((draft or {}).get("subject") or "").strip()
    if not body:
        return Review(0, ["Body is empty."], {})

    dims, issues = {}, []
    words = body.split()
    n_words = len(words)
    sentences = _split_sentences(body)

    # 1) Personalization — does it reference a grounded specific?
    grounded = _grounded_tokens(data)
    body_tokens = set(re.findall(r"[A-Za-z0-9]{3,}", body.lower()))
    overlap = len(grounded & body_tokens)
    if not grounded:
        dims["personalization"] = 0.6          # nothing to ground on (thin data)
    elif overlap >= 2:
        dims["personalization"] = 1.0
    elif overlap == 1:
        dims["personalization"] = 0.7
    else:
        dims["personalization"] = 0.2
        issues.append("Reference a specific grounded detail about the company, "
                      "not a generic observation.")

    # 2) CTA / curiosity: every sendable email needs a clear, low-friction next
    #    step. Passive sign-offs created too many "safe but average" drafts.
    last_line = sentences[-1] if sentences else ""
    last_low = last_line.lower()
    body_low = body.lower()
    hard_cta = bool(re.search(
        r"\b(book|schedule|set up|hop on|jump on|calendar|calendly|demo|"
        r"30[- ]min|quick call this week|are you (free|available))\b",
        last_low))
    clear_offer = bool(re.search(
        r"\b(send|show|share|drop|write)\b.{0,50}\b(example|one|draft|note|email)\b",
        last_low,
    ))
    vague_cta = any(p in last_low for p in _VAGUE_CTAS)
    passive = any(p in last_low for p in _PASSIVE_ENDINGS)
    public_route_only = bool(
        not (data.get("primary_contact_name") or data.get("founder_name"))
        and (data.get("public_contact_email") or data.get("contact_page_url")
             or data.get("recipient_route"))
    )
    wrong_public_question = public_route_only and bool(re.search(
        r"\bare you (the )?(one|person|right person)\b", last_low))

    if (body.rstrip().endswith("?") or "?" in body[-160:]) and not (
            hard_cta or vague_cta or passive or wrong_public_question):
        dims["cta"] = 1.0
    elif clear_offer and not (hard_cta or passive):
        dims["cta"] = 0.9
    else:
        dims["cta"] = 0.3
        if wrong_public_question:
            issues.append("This is a public company route, not a named person; "
                          "ask to be pointed to the right owner instead of asking "
                          "whether they are the person.")
        elif vague_cta or passive:
            issues.append("Replace the passive/vague ending with a clear, "
                          "low-friction CTA tied to the company context.")
        else:
            issues.append("End on a clear low-friction CTA tied to the company "
                          "context, not a hard sales ask.")

    # 3) Founder voice — contractions present, no throat-clearing opener.
    has_contraction = bool(_CONTRACTION_RE.search(body))
    first_line = body.lower().lstrip()[:60]
    weak_open = any(first_line.startswith(o) for o in _WEAK_OPENERS)
    generic_hits = [p for p in _GENERIC_FOUNDER_OUTBOUND if p in body.lower()]
    dims["founder_voice"] = 1.0 if (has_contraction and not weak_open) else 0.5
    if generic_hits:
        dims["founder_voice"] = min(dims["founder_voice"], 0.55)
        issues.append("Replace generic founder-outbound phrasing with the exact "
                      "prospect problem or proof point on file.")
    if weak_open:
        issues.append("Rewrite the opening — it reads like a generic cold-email "
                      "opener; drop the wind-up and state the observation.")

    # 4) Naturalness — varied sentence length AND varied sentence openings
    #    (repeated first words, e.g. "I ... I ... I", read robotic).
    lengths = [len(s.split()) for s in sentences if s.split()]
    natural = 1.0
    if len(lengths) >= 3:
        spread = max(lengths) - min(lengths)
        if spread < 6:
            natural = min(natural, 0.55)
            issues.append("Vary sentence length — mix a short punchy line with a "
                          "longer one; the rhythm is too uniform.")
    firsts = [s.split()[0].lower().strip(",.") for s in sentences if s.split()]
    if len(firsts) >= 3:
        top = max((firsts.count(w) for w in set(firsts)), default=0)
        if top >= 3:
            natural = min(natural, 0.5)
            issues.append("Too many sentences start with the same word; vary the "
                          "sentence openings so it doesn't read like a list.")
    dims["naturalness"] = natural if len(lengths) >= 3 else 0.8

    # 5) Readability — sane length band (soft; validator owns hard caps).
    if 25 <= n_words <= 105:
        dims["readability"] = 1.0
    elif n_words < 25:
        dims["readability"] = 0.5
    else:
        dims["readability"] = 0.7

    # 6) Clarity — a usable subject exists and isn't empty/too long.
    dims["clarity"] = 1.0 if (0 < len(subject) <= 90) else 0.5

    score = round(100 * sum(dims.values()) / len(dims))
    return Review(score, issues, dims)


def _split_sentences(text: str) -> list:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if any(c.isalpha() for c in p)]


# ── Quality report (deterministic display; reuses review(), NO model call) ──
# Spam-filter tripwires: urgency/greed/hype words that hurt deliverability.
_SPAMMY = (
    "free", "guarantee", "act now", "limited time", "click here", "buy now",
    "risk-free", "100%", "cash", "winner", "congratulations", "urgent",
    "offer expires", "cheap", "discount", "$$$", "double your", "no obligation",
    "special promotion", "why pay more", "amazing", "incredible offer",
)
_READ_WPM = 200


def quality_report(draft: dict, data: dict) -> dict:
    """A display-ready quality breakdown for ONE draft. Built entirely from the
    existing deterministic review() plus a spam/reading pass — no model call.

    Returns the labels the UI shows: overall (0-100), hook, personalization,
    founder_voice, cta, reply_likelihood (all 0-100), spam_risk ("low"/"medium"/
    "high"), reading_seconds.
    """
    rev = review(draft, data)
    d = rev.dimensions
    body = _strip_ps((draft or {}).get("body") or "").strip()
    subject = ((draft or {}).get("subject") or "").strip()
    words = len(body.split())

    def pct(key, default=0.5):
        return round(100 * d.get(key, default))

    # Hook = strength of the opening: grounded specificity + genuine voice.
    hook = round(100 * (0.6 * d.get("personalization", 0.5)
                        + 0.4 * d.get("founder_voice", 0.5)))
    # Reply likelihood = a weighted blend of what actually earns a reply.
    reply = round(100 * (0.30 * d.get("personalization", 0.5)
                         + 0.25 * d.get("cta", 0.5)
                         + 0.25 * d.get("founder_voice", 0.5)
                         + 0.20 * d.get("naturalness", 0.8)))
    return {
        "overall": rev.score,
        "hook": hook,
        "personalization": pct("personalization"),
        "founder_voice": pct("founder_voice"),
        "cta": pct("cta"),
        "reply_likelihood": reply,
        "spam_risk": _spam_risk(subject, body),
        "reading_seconds": max(5, round(words / _READ_WPM * 60)),
    }


def _spam_risk(subject: str, body: str) -> str:
    text = (subject or "") + " " + (body or "")
    low = text.lower()
    score = min(3, text.count("!"))                    # exclamation marks
    score += sum(1 for w in _SPAMMY if w in low)       # spammy phrases
    caps = sum(1 for w in re.findall(r"[A-Za-z]{3,}", text) if w.isupper())
    score += min(2, caps)                              # ALL-CAPS shouting
    if re.search(r"https?://|www\.", low):
        score += 1                                     # raw links
    return "high" if score >= 4 else "medium" if score >= 2 else "low"


# ── Explain a rewrite (deterministic, ≤2 short sentences) ─────────────
def explain_change(old_body: str, new_body: str) -> str:
    """A short, human explanation of what a rewrite changed vs. preserved.

    Deterministic (no model call): compares word count, opening line, and closing
    question between the previous and new body. Returns at most two sentences.
    """
    old_body = (old_body or "").strip()
    new_body = (new_body or "").strip()
    if not old_body or not new_body:
        return ""
    parts = []
    dw = len(new_body.split()) - len(old_body.split())
    if dw <= -5:
        parts.append(f"Tightened it by {abs(dw)} words")
    elif dw >= 5:
        parts.append(f"Expanded it by {dw} words")

    old_s = _split_sentences(old_body)
    new_s = _split_sentences(new_body)
    if old_s and new_s and _norm(old_s[0]) != _norm(new_s[0]):
        parts.append("reworked the opening")
    old_q = _last_question(old_s)
    new_q = _last_question(new_s)
    if old_q and new_q and _norm(old_q) != _norm(new_q):
        parts.append("changed the closing ask")
    elif old_q and _norm(old_q) == _norm(new_q):
        parts.append("kept the CTA")

    if not parts:
        return "Made the edit while keeping the hook and structure."
    lead = parts[0][0].upper() + parts[0][1:]
    rest = parts[1:]
    if not rest:
        return lead + "."
    if len(rest) == 1:
        return f"{lead}, and {rest[0]}."
    return f"{lead}. " + (", ".join(rest[:-1]) + f", and {rest[-1]}.").capitalize()


def _last_question(sentences: list):
    for s in reversed(sentences):
        if s.rstrip().endswith("?"):
            return s
    return None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())
