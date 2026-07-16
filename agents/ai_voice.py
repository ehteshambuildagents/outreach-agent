"""Structural "sounds like AI" detector (deterministic, no model call).

The banned-phrase list in ``writer_prompt`` catches AI *wording* ("leverage",
"I hope this finds you well"). This module catches AI *structure* — the shape of
the sentence, not its vocabulary — which is what actually survives a paraphrase
and still trips a detector (and a human's gut):

  * tricolon / rule-of-three   "speed, scale, and precision"
  * antithesis                 "not X, but Y" / "it's not just X, it's Y"
  * participial tails          "..., ensuring seamless delivery"
  * metronome rhythm           several sentences of near-identical length
  * anaphora                   several sentences starting with the same word

It is ONE source of truth, imported by three callers so the rule can't drift:
  * ``writer_review`` — feeds tells into the bounded regeneration loop (a weak
    draft is rewritten with NO extra model call).
  * ``guard.deliverability`` — an independent pre-send gate: structure this
    robotic contributes real risk and, compounded, BLOCKS the send.
  * ``eval.writer_detector`` — a local proxy score (0-100) that approximates an
    external AI-content detector so a batch can be measured without the network.

Everything here is pure Python and never raises; a bad input scores 0 tells.
"""

import re
import statistics

from agents.writer_prompt import BANNED_MATCH, stem_present

# ── Sentence / word tokenisers (self-contained; no cross-import) ──────────
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
# Abbreviations we must not split on (mirrors writer_validator, kept local so
# this module has no import cycle with the validator).
_ABBR = ("e.g.", "i.e.", "mr.", "mrs.", "ms.", "dr.", "vs.", "inc.", "co.",
         "ltd.", "u.s.", "u.k.", "y.c.", "a.i.", "ph.d.")


def split_sentences(text: str) -> list:
    """Sentences with at least one letter (P.S. lines and empties dropped)."""
    if not text or not text.strip():
        return []
    protected = re.sub(r"(\d)\.(\d)", r"\1<dot>\2", text)
    for abbr in _ABBR:
        protected = re.sub(r"(?<![A-Za-z])" + re.escape(abbr),
                           abbr.replace(".", "<dot>"), protected,
                           flags=re.IGNORECASE)
    parts = _SENT_SPLIT.split(protected.strip())
    out = []
    for part in parts:
        part = part.replace("<dot>", ".").strip()
        if any(ch.isalpha() for ch in part):
            out.append(part)
    return out


# ── Structural tell detectors ─────────────────────────────────────────────
# A "small" list item for a tricolon: 1-3 words, not starting with a capital
# (proper nouns like "Gmail, Outlook, and Slack" are a real list, not filler).
_TRICOLON_RE = re.compile(
    r"\b([a-z][a-z'-]+(?:\s+[a-z][a-z'-]+){0,2}),\s+"
    r"([a-z][a-z'-]+(?:\s+[a-z][a-z'-]+){0,2}),?\s+and\s+"
    r"([a-z][a-z'-]+(?:\s+[a-z][a-z'-]+){0,2})\b"
)
# "not (just/only) X, but (also) Y"  /  "it's not about X, it's about Y"  /
# "it's not just X, it's Y" (the comma form, no "but" — a common self-audit gap).
_ANTITHESIS_RES = (
    re.compile(r"\bnot\s+(?:just|only|merely|simply)?\s*[\w'-]+(?:\s+[\w'-]+){0,5}?[,]?\s+but(?:\s+also)?\b",
               re.IGNORECASE),
    re.compile(r"\b(?:isn't|aren't|wasn't|it's|its)\s+not\s+(?:just\s+)?about\b.{1,50}?,\s*it'?s\s+about\b",
               re.IGNORECASE),
    re.compile(r"\b(?:it'?s|that'?s|this is)\s+not\s+(?:just|only|merely|simply)\s+"
               r"[\w'-]+(?:\s+[\w'-]+){0,4}?,\s*(?:it'?s|that'?s|but it'?s)\b",
               re.IGNORECASE),
)
# LLM's favourite subordinate-clause tail: ", <gerund> ..."
_PARTICIPIAL_RE = re.compile(
    r",\s+(ensuring|allowing|enabling|empowering|helping|driving|delivering|"
    r"making|providing|offering|streamlining|unlocking|transforming|"
    r"leveraging|maximizing|maximising|optimizing|optimising|boosting|"
    r"ensuring that|allowing you to|helping you|helping teams)\b",
    re.IGNORECASE,
)
# Setup -> contrast-pivot: name a pain point, then pivot to the pitch with "the
# other side of that" / "the opposite of that" / "flip side". A default AI move.
# ("instead of that" is handled separately — it needs first-person pitch context,
# since "we picked X instead of that" is ordinary human writing.)
_PIVOT_RE = re.compile(
    r"\b(the other side of (?:that|what|this)|the other end of that|"
    r"the opposite of (?:that|what)|doing the opposite|"
    r"the flip side|flip side of that)\b",
    re.IGNORECASE,
)
_PIVOT_FIRST_PERSON_RE = re.compile(
    r"\b(i'm|i am|i've|i built|i made|i do|i help|what i)\b", re.IGNORECASE)

# Points each tell contributes to the guard's risk score / the proxy AI-score.
# Tuned so ONE tell is a nudge, but two or three compound toward a block.
_W_TRICOLON = 10
_W_ANTITHESIS = 12
_W_PARTICIPIAL = 9
_W_RHYTHM = 12
_W_ANAPHORA = 10
_W_PIVOT = 12
_W_BINARY_CLOSER = 12
_W_EMDASH = 8
_W_SEMICOLON = 8


def scan(text: str) -> list:
    """Return ALL structural AI-tells in ``text`` as (label, weight) tuples.

    Empty when the text reads human. Deterministic and order-stable so callers
    can show the first N and the weights sum reproducibly. This is the full set
    (sentence SHAPE + whole-email RHYTHM); the guard and the proxy score use it.
    """
    return _shape_scan(text or "") + _rhythm_scan(text or "")


def _shape_scan(text: str) -> list:
    """Tells about the shape of individual sentences: tricolon, antithesis,
    participial tails, the setup->pitch pivot, and the forced-binary closer. Kept
    separate so ``writer_review`` (which already has its own rhythm/anaphora
    checks) can consume just these via ``shape_tells`` without double-flagging."""
    tells = []
    sents = split_sentences(text)
    for a, b, c in _TRICOLON_RE.findall(text)[:2]:      # cap: don't over-count
        tells.append((f'rule-of-three list ("{a}, {b}, and {c}")', _W_TRICOLON))

    for rx in _ANTITHESIS_RES:
        m = rx.search(text)
        if m:
            tells.append((f'"not X, but Y" antithesis ("{_snip(m.group(0))}")',
                          _W_ANTITHESIS))
            break                                       # one is enough to flag

    for g in _PARTICIPIAL_RE.findall(text)[:3]:
        tells.append((f'participial tail (", {g.lower()} ...")', _W_PARTICIPIAL))

    pivot = _contrast_pivot(sents)
    if pivot:
        tells.append((f'setup-then-pitch pivot ("{_snip(pivot)}")', _W_PIVOT))

    closer = _binary_closer(sents)
    if closer:
        tells.append((f'forced binary-choice closer ("{_snip(closer)}")',
                      _W_BINARY_CLOSER))
    return tells


def _contrast_pivot(sents: list):
    """The AI move from naming a pain point to selling: a first-person pitch that
    pivots on "the other side of that" / "the opposite of that" / "flip side".
    Returns the offending sentence or None. The "instead of that" variant only
    counts with first-person build language, so a plain contrast ("we use X
    instead of that") isn't flagged."""
    for s in sents:
        low = s.lower()
        if _PIVOT_RE.search(low):
            return s
        if "instead of that" in low and _PIVOT_FIRST_PERSON_RE.search(low):
            return s
    return None


# A short tag that RESTATES a choice ("Which is it?", "So which one?", "Either?").
_CHOICE_TAG_RE = re.compile(r"^(?:so\s+)?(?:which|either)\b", re.IGNORECASE)


def _binary_closer(sents: list):
    """A forced either/or closer capped with a short choice-restating tag ("...,
    or Y. Which is it?"). Returns the tag sentence or None. Three conditions keep
    it precise: the prior clause must present an explicit "..., or ..." choice, the
    last sentence must be a SHORT tag (<=4 words), and that tag must restate the
    choice (which/either) — so a normal single either/or question ("are you the
    right person, or should I ask someone else?") or a plain "sound good?" is not
    a false positive. Note the either/or clause may end in "." (an indirect
    question), which is exactly the ZeroGPT-flagged case."""
    if len(sents) < 2:
        return None
    last = sents[-1].strip()
    prev = sents[-2].strip()
    last_core = last.rstrip("?!. ")
    is_short_tag = last.endswith("?") and len(last_core.split()) <= 4
    restates_choice = bool(_CHOICE_TAG_RE.search(last_core))
    prev_has_alt = bool(re.search(r",\s+or\b", prev.lower())
                        or re.search(r",\s+or\b", last.lower()))
    return last if (is_short_tag and restates_choice and prev_has_alt) else None


def _snip(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()[:48]


def _rhythm_scan(text: str) -> list:
    """Whole-email rhythm tells: metronomic sentence length + repeated openers."""
    tells = []
    sents = split_sentences(text)
    lengths = [len(s.split()) for s in sents if s.split()]
    # Metronome rhythm: 4+ sentences whose lengths barely vary. Real people mix a
    # 3-word line with a 20-word one; a low spread AND low stdev is the tell.
    if len(lengths) >= 4:
        spread = max(lengths) - min(lengths)
        try:
            stdev = statistics.pstdev(lengths)
        except statistics.StatisticsError:
            stdev = 0.0
        if spread <= 5 and stdev < 3.0:
            tells.append(("uniform sentence length (metronome rhythm)", _W_RHYTHM))

    # Anaphora: 3+ sentences opening on the same word ("I ... I ... I ...").
    firsts = [s.split()[0].lower().strip(",.:;") for s in sents if s.split()]
    if len(firsts) >= 3:
        top_word, top_n = _most_common(firsts)
        if top_n >= 3 and top_word not in ("the", "a", "an"):
            tells.append((f'repeated sentence opener ("{top_word} ...", x{top_n})',
                          _W_ANAPHORA))

    # Self-audit adds: two whole-email formatting crutches. Thresholds are >=2 (a
    # single em dash or semicolon is ordinary human writing; the AI tell is the
    # REPEAT). Placed here (not in shape) because the guard is the real consumer —
    # the writer strips em dashes upstream, so its own output never trips these.
    if len(re.findall(r"[—–]", text)) >= 2:
        tells.append(("em-dash overuse (rhythm crutch)", _W_EMDASH))
    if text.count(";") >= 2:
        tells.append(("semicolon-heavy compound sentences", _W_SEMICOLON))
    return tells


def tells(text: str) -> list:
    """Just the human-readable labels of ALL structural tells (no weights)."""
    return [label for label, _ in scan(text)]


def shape_tells(text: str) -> list:
    """Labels of only the sentence-SHAPE tells (tricolon / antithesis /
    participial tail / setup-then-pitch pivot / forced binary closer) — the ones
    ``writer_review`` doesn't already cover with its own rhythm checks."""
    return [label for label, _ in _shape_scan(text or "")]


def penalty(text: str) -> int:
    """Total structural risk in ``text`` (sum of tell weights), capped at 100."""
    return min(100, sum(w for _, w in scan(text)))


# ── Banned-phrase reuse (wording tells, shared list) ──────────────────────
def banned_hits(text: str) -> list:
    """Readable labels of banned AI *phrases* present (reuses the writer's list
    so wording and structure are measured against one dictionary)."""
    low = (text or "").lower()
    return [readable for readable, stem in BANNED_MATCH if stem_present(stem, low)]


# ── Proxy AI-content score (0-100) for offline measurement ────────────────
# NOT a substitute for a real detector (GPTZero / Originality.ai); it is a
# transparent, deterministic approximation built from the same tells we ban, so
# a batch can be regression-tested without a paid API or the network. Calibrated
# on the writer's own good samples (~<15) vs deliberately-generic copy (~>45).
_CONTRACTION_RE = re.compile(r"\b\w+['’](?:s|re|ve|ll|d|t|m)\b|\bn['’]t\b", re.IGNORECASE)


def ai_score(text: str) -> int:
    """A 0-100 "reads like AI" proxy. Higher = more machine-sounding.

    Blends banned wording, structural tells, and two whole-email signals
    (metronomic rhythm already in scan(); absence of any contraction, which is a
    strong robotic tell in a founder email). Deterministic; never raises.
    """
    body = (text or "").strip()
    if not body:
        return 0
    score = 0
    score += 14 * len(banned_hits(body))               # each banned phrase is loud
    score += penalty(body)                              # structural tells

    sents = split_sentences(body)
    words = body.split()
    # A cold founder email with zero contractions reads stiff/generated.
    if words and not _CONTRACTION_RE.search(body):
        score += 12
    # Long, even sentences with no short punchy line: add a little more.
    lengths = [len(s.split()) for s in sents if s.split()]
    if len(lengths) >= 3 and min(lengths) >= 12:
        score += 8
    return max(0, min(100, score))


# ── tiny helper ───────────────────────────────────────────────────────────
def _most_common(items):
    best, best_n = None, 0
    for w in set(items):
        n = items.count(w)
        if n > best_n:
            best, best_n = w, n
    return best, best_n
