"""Output validation + automatic repair for the email writer.

Two responsibilities:
  * ``repair`` — deterministic fixes that need NO model call (strip a stray
    "Subject:" prefix or wrapping quotes, remove any model-added P.S., append the
    canonical reveal P.S. when asked, drop a guessed greeting name when no
    founder is known).
  * ``validate`` — return a list of human-readable problems (empty == clean):
    subject/body present, 3-5 sentences, no banned wording, no placeholders, no
    generic/guessed greeting, sane subject, reveal-P.S. discipline.

Only banned wording and sentence count cannot be repaired deterministically;
those are what trigger writer.py's single bounded regeneration.
"""

import re

from agents.writer_prompt import BANNED_MATCH, first_name, stem_present
from config.settings import (
    WRITER_MAX_SENTENCES,
    WRITER_MAX_WORDS,
    WRITER_MIN_SENTENCES,
    WRITER_SUBJECT_MAX_CHARS,
)

# Em/en dashes are a hard ban (ban #6). This is the deterministic backstop: an
# em dash becomes a comma, an en dash a hyphen. Applied in repair().
_EMDASH_RE = re.compile(r"\s*—\s*")


def _normalize_dashes(text: str) -> str:
    if not text:
        return text
    text = _EMDASH_RE.sub(", ", text)          # em dash -> comma
    text = text.replace("–", "-")          # en dash -> hyphen
    text = re.sub(r"\s+,", ",", text)           # tidy " ,"
    text = re.sub(r",\s*,", ",", text)          # collapse ",,"
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text

# A P.S. line: from "P.S"/"PS" (any spacing/punctuation) to the end of the text.
_PS_RE = re.compile(r"(?:^|\n)\s*p\.?\s?s\.?\b.*$", re.IGNORECASE | re.DOTALL)

# Leftover template scaffolding the model must never emit.
_PLACEHOLDER_RE = re.compile(
    r"\[[^\]]+\]|\{[^}]+\}|<[A-Za-z][^>]*>|\b(?:TODO|FIXME|XXXX?|lorem ipsum)\b",
    re.IGNORECASE,
)

# Sentence-splitter guards: don't split inside these abbreviations / decimals.
# Matched only at a word boundary (see count_sentences) so "st." never fires
# inside "fast." — that class of bug silently merges two sentences into one.
_ABBREVIATIONS = (
    "e.g.", "i.e.", "mr.", "mrs.", "ms.", "dr.", "vs.", "inc.", "co.", "ltd.",
    "u.s.", "u.k.", "y.c.", "a.i.", "ph.d.",
)

# Greeting that addresses a person by (capitalised) name. Only the leading letter
# is case-flexible — keeping [A-Z] case-sensitive means "Hey there" (lowercase t)
# is NOT mistaken for a name, while "Hey Bob" / "hey Bob" both match.
_GREETING_NAME_RE = re.compile(r"\s*(?:[Hh]ey|[Hh]i|[Hh]ello)\s+([A-Z][A-Za-z'’\-]+)")
_GREETING_REPAIR_RE = re.compile(
    r"\s*(?:[Hh]ey|[Hh]i|[Hh]ello)\s+([A-Z][A-Za-z'’\-]+)\s*[—\-,:]*\s*"
)


# ── Reveal P.S. (canonical, deterministic) ─────────────────────────────
def build_reveal_ps(company) -> str:
    """The tasteful AI-reveal P.S. (only appended when add_reveal=True)."""
    target = company.strip() if isinstance(company, str) and company.strip() else None
    subject = target or "your site"
    return (
        f"P.S. an AI agent I built wrote this whole email after researching "
        f"{subject}. If it felt human, that's the point. That's the tool."
    )


# ── Repair (no model call) ─────────────────────────────────────────────
def repair(draft: dict, data: dict, add_reveal: bool) -> dict:
    subject = _tidy(draft.get("subject"))
    body = _tidy(draft.get("body"))

    subject = re.sub(r"^\s*subject\s*:\s*", "", subject, flags=re.IGNORECASE).strip()
    subject = _unwrap_quotes(subject)
    body = _unwrap_quotes(body)

    subject = _normalize_dashes(subject)         # ban #6, deterministic backstop
    body = _normalize_dashes(body)

    body = _strip_ps(body)                       # we own the P.S. entirely
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    body = _repair_greeting(body, data)

    if add_reveal and body:
        body = body.rstrip() + "\n\n" + build_reveal_ps(data.get("company_name"))

    return {"subject": subject, "body": body}


# ── Validation ─────────────────────────────────────────────────────────
def validate(draft: dict, data: dict, add_reveal: bool) -> list:
    problems = []
    subject = (draft.get("subject") or "").strip()
    body = (draft.get("body") or "").strip()

    if not subject:
        problems.append("Subject line is missing.")
    if not body:
        problems.append("Email body is missing.")
        return problems  # nothing else is checkable without a body

    core = _strip_ps(body)

    sentences = count_sentences(core)
    if sentences < WRITER_MIN_SENTENCES:
        problems.append(
            f"Body is too thin ({sentences} sentences); write at least "
            f"{WRITER_MIN_SENTENCES}."
        )
    elif sentences > WRITER_MAX_SENTENCES:
        problems.append(
            f"Body has too many sentences ({sentences}); keep it to at most "
            f"{WRITER_MAX_SENTENCES}."
        )

    words = len(core.split())
    if words > WRITER_MAX_WORDS:
        problems.append(
            f"Body is too long ({words} words); cut it under 90."
        )

    if len(subject) > WRITER_SUBJECT_MAX_CHARS:
        problems.append("Subject line is too long; keep it short and personal.")

    banned = find_banned(subject + "\n" + core, _allowed_terms(data))
    if banned:
        problems.append("Remove banned wording: " + ", ".join(banned) + ".")

    if _PLACEHOLDER_RE.search(subject) or _PLACEHOLDER_RE.search(core):
        problems.append("Remove placeholder text (e.g. [Company], {name}).")

    if re.search(r"\b(?:hey|hi|hello)\s+there\b", core, re.IGNORECASE):
        problems.append('Do not use a generic greeting like "Hey there".')

    greeting_problem = _greeting_name_problem(core, data)
    if greeting_problem:
        problems.append(greeting_problem)

    if _looks_title_cased(subject):
        problems.append(
            "Subject looks like a title-cased campaign line; make it lowercase "
            "and personal."
        )

    if add_reveal and not _has_reveal_ps(body):
        problems.append("Reveal P.S. is required but missing.")
    if (not add_reveal) and _strip_ps(body) != body:
        problems.append("No P.S./sign-off allowed when reveal mode is off.")

    return problems


# ── Banned wording ─────────────────────────────────────────────────────
def find_banned(text: str, allowed_terms) -> list:
    """Banned phrases present in `text`, ignoring matches that are part of an
    allowed proper noun (so a company literally named 'Acme Solutions' or a
    customer 'Salesforce' never trips the 'solutions'/'leverage' stems)."""
    scan = (text or "").lower()
    allowed_stems = {
        str(term).split(":", 1)[1].lower()
        for term in (allowed_terms or ())
        if isinstance(term, str) and term.startswith("stem:")
    }
    for term in allowed_terms or ():
        if term and not (isinstance(term, str) and term.startswith("stem:")):
            scan = scan.replace(term.lower(), " ")
    hits = []
    for readable, stem in BANNED_MATCH:
        if stem in allowed_stems:
            continue
        if stem_present(stem, scan) and readable not in hits:
            hits.append(readable)
    return hits


def _allowed_terms(data: dict) -> list:
    terms = []
    for key in ("company_name", "founder_name"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            terms.append(value.strip())
    for key in ("notable_customers", "tech_stack"):
        value = data.get(key)
        if isinstance(value, list):
            terms += [str(item) for item in value if str(item).strip()]
    evidence = " ".join(str(data.get(k) or "") for k in (
        "unique_hook", "recent_focus", "what_they_do",
        "their_mission_or_why", "competitive_positioning"))
    evidence += " " + " ".join(str(item) for item in data.get("additional_hooks") or [])
    # Several banned words ("transform", "seamless", "unlock" ...) are useful
    # anti-AI-writing bans BUT are also common in real company positioning. When a
    # stem is genuinely present in the grounded research, don't fail a draft solely
    # for echoing the company's OWN language — that's specificity, not filler. (The
    # pure connective/hedge tells, e.g. "that said" or "no worries", are never
    # legitimate grounding, so they stay hard-banned regardless.)
    low_evidence = evidence.lower()
    for stem in _GROUNDED_EXEMPTIBLE_STEMS:
        if stem in low_evidence:
            terms.append("stem:" + stem)
    return terms


# Banned stems that MAY legitimately appear in a company's grounded positioning;
# exempted only when actually present in that company's research (see above).
_GROUNDED_EXEMPTIBLE_STEMS = (
    "transform", "seamless", "unlock", "cutting-edg", "best-in-class",
    "world-class",
)


# ── Sentence counting ──────────────────────────────────────────────────
def count_sentences(text: str) -> int:
    if not text or not text.strip():
        return 0
    protected = re.sub(r"(\d)\.(\d)", r"\1<dot>\2", text)   # keep decimals intact
    for abbr in _ABBREVIATIONS:
        protected = re.sub(
            r"(?<![A-Za-z])" + re.escape(abbr), abbr.replace(".", "<dot>"),
            protected, flags=re.IGNORECASE,
        )
    parts = re.split(r"[.!?]+(?:\s+|$)", protected)
    return sum(1 for part in parts if any(ch.isalpha() for ch in part))


# ── Greeting discipline ────────────────────────────────────────────────
def _allowed_greeting_firsts(data: dict) -> set:
    """First names it is legitimate to greet: the primary contact, the founder,
    AND any verified team member (so a revision can retarget to, say, the CTO).
    All are grounded upstream, so this never opens the door to a guessed name."""
    firsts = set()
    for key in ("primary_contact_name", "founder_name"):
        got = first_name(data.get(key))
        if got:
            firsts.add(got.lower())
    for member in data.get("team_members") or []:
        if isinstance(member, dict):
            got = first_name(member.get("name"))
            if got:
                firsts.add(got.lower())
    return firsts


def _greeting_name_problem(core: str, data: dict):
    match = _GREETING_NAME_RE.match(core)
    if not match:
        return None
    greeted = match.group(1)
    # Any grounded person on file (primary contact, founder, or a team member)
    # is legitimate to greet — this is what lets "target the CTO" work.
    allowed = _allowed_greeting_firsts(data)
    company_first = (first_name(data.get("company_name")) or "").lower()

    if greeted.lower() == company_first and company_first:
        return None  # "Hey Acme —" addresses the company by name — fine
    if not allowed:
        return (
            "Email greets a person by name, but research has no verified contact "
            "name — do not invent a name; open with the company instead."
        )
    if greeted.lower() not in allowed:
        return (
            f"Greeting name '{greeted}' is not a verified person on file; greet a "
            "known contact/team member or open with the company."
        )
    return None


def _repair_greeting(body: str, data: dict) -> str:
    """If no verified person is known and the draft still greets someone by name,
    drop the name to a clean company-anchored opener. (When a real person IS on
    file we leave the greeting alone — a wrong name is fixed by regeneration.)"""
    if _allowed_greeting_firsts(data):
        return body
    match = _GREETING_REPAIR_RE.match(body)
    if not match:
        return body
    company_first = (first_name(data.get("company_name")) or "").lower()
    if match.group(1).lower() == company_first and company_first:
        return body  # company opener, not a guessed person
    rest = body[match.end():].lstrip(" —-,:")
    return f"Hey, {rest}".rstrip() if rest else "Hey,"


# ── Subject heuristics ─────────────────────────────────────────────────
def _looks_title_cased(subject: str) -> bool:
    """True only for clearly campaign-style subjects where EVERY significant
    word is capitalised ("Partnership Opportunity", "Boost Your Sales")."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’\-]*", subject) if len(w) >= 3]
    if len(words) < 2:
        return False
    return all(word[0].isupper() for word in words)


# ── P.S. helpers ───────────────────────────────────────────────────────
def _strip_ps(body: str) -> str:
    return _PS_RE.sub("", body or "").strip()


def _has_reveal_ps(body: str) -> bool:
    match = _PS_RE.search(body or "")
    if not match:
        return False
    return re.search(r"\bai\b", match.group(0), re.IGNORECASE) is not None


# ── Small text utilities ───────────────────────────────────────────────
def _tidy(value) -> str:
    if value is None:
        return ""
    text = str(value)
    text = "".join(ch for ch in text if ch >= " " or ch == "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _unwrap_quotes(text: str) -> str:
    if len(text) >= 2 and text[0] in "\"'“‘" and text[-1] in "\"'”’":
        return text[1:-1].strip()
    return text
