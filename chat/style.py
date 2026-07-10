"""Writing-style memory: learn how the user likes their emails, quietly.

Preferences are held with CONFIDENCE, not as a hard switch. A one-off "make it
more technical" nudges a preference a little; repeating it raises confidence;
asking for the opposite lowers it; and if a preference isn't reinforced it
decays over time. The latest explicit instruction always wins for the email in
front of us (the writer applies `guidance` directly) — the profile only shapes
the DEFAULT for future drafts. So nothing the user says once gets locked in.

A ``WritingProfile`` wraps this and carries a ``kind`` (founder / sales /
enterprise / recruiting / personal) so different profile types can share the same
storage and rendering without any change to how it's persisted.

Nothing here is ever shown to the user. Deterministic, no model call.

Persisted shape (JSON-safe, v2 — v1 dicts are migrated on load):
    {"v": 2, "kind": "founder",
     "prefs":  {"no_emoji": {"confidence": 0.6, "count": 1, "last": 1700000000.0}},
     "avoid":  ["i hope you're well", ...],
     "avoid_meta": {"i hope you're well": {"confidence": 0.8, "last": ...}},
     "edits": 7}
"""

import re
import time

# ── Confidence model ──────────────────────────────────────────────────
_ACTIVATION = 0.5            # a preference is applied once its live confidence >= this
_AVOID_ACTIVATION = 0.4     # avoid-phrases apply at a lower bar (a "never say X" is firm)
_HALF_LIFE_DAYS = 30.0      # unreinforced preference confidence halves every 30 days
_AVOID_HALF_LIFE_DAYS = 90.0
_DAY = 86400.0
_MAX_CONFIDENCE = 1.0
_EXPLICIT_WEIGHT = 0.6      # explicit style statements ("no emojis") land immediately
_SOFT_WEIGHT = 0.34         # tone/length nudges need repeating to become standing
_CONFLICT_DECAY = 0.5       # asking for the opposite halves a preference's confidence

# Standing-preference cues. key, triggers, prompt instruction, is_explicit.
_CUES = (
    ("lowercase_subject", ("lowercase", "lower case", "lower-case"),
     "keep the subject line lowercase", True),
    ("no_emoji", ("no emoji", "no emojis", "without emoji", "remove emoji",
                  "drop the emoji"), "never use emojis", True),
    ("no_fluff", ("no fluff", "less salesy", "not salesy", "cut the fluff",
                  "no filler", "less fluff", "drop the fluff"),
     "no fluff or salesy filler — every line earns its place", True),
    ("casual", ("casual", "informal", "relaxed", "chill"),
     "keep the tone casual and relaxed", False),
    ("formal", ("more formal", "professional tone", "less casual"),
     "keep the tone a touch more formal", False),
    ("warm", ("warmer", "friendlier", "more warmth"),
     "keep it warm and personable", False),
    ("direct", ("more direct", "blunter", "to the point", "get to the point",
                "less wordy"), "be direct and get to the point", False),
    ("technical", ("more technical", "technical tone", "for engineers"),
     "lean technical and precise", False),
    ("founder", ("more founder", "founder voice", "founder-like", "like a founder"),
     "sound like a founder, peer to peer", False),
    ("short", ("shorter", "make it short", "too long", "keep it short",
               "more concise", "trim it"), "keep it short and punchy", False),
    ("direct_cta", ("stronger cta", "clearer cta", "clear ask", "direct cta",
                    "better call to action"), "make the closing ask clear and easy",
     False),
)
_CUE_BY_KEY = {key: (triggers, instr, explicit)
               for key, triggers, instr, explicit in _CUES}

# Opposing preferences: reinforcing one weakens the other (a real change of mind).
_CONFLICTS = {"casual": "formal", "formal": "casual",
              "warm": "direct", "direct": "warm"}

# Profile kinds (future-proofing: same storage + rendering for each).
KINDS = ("founder", "sales", "enterprise", "recruiting", "personal")

# ── Avoid-phrase extraction (unchanged behaviour) ─────────────────────
_AVOID_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bnever (?:say|use|write|start with)\s+(.+)",
    r"\b(?:don'?t|do not) (?:say|use|write|start with)\s+(.+)",
    r"\bstop (?:saying|using|writing)\s+(.+)",
    r"\bno more\s+(.+)",
    r"\bget rid of (?:the\s+)?(.+)",
    r"\bremove (?:the\s+)?(.+?)(?:\s+line)?$",
    r"\bdrop (?:the\s+)?(.+?)(?:\s+line)?$",
    r"\bavoid (?:saying|using)?\s*(.+)",
))
_AVOID_MAX = 12
_AVOID_PHRASE_MAX_WORDS = 8
_QUOTED_RE = re.compile(
    r'"([^"]{2,60})"' r"|'([^']{2,60})'" r"|“([^”]{2,60})”" r"|‘([^’]{2,60})’")


def _quoted_spans(text: str):
    for m in _QUOTED_RE.finditer(text):
        yield next(g for g in m.groups() if g is not None)


def _clean_phrase(text: str) -> str:
    text = text.strip()
    text = re.split(r"\s*(?:,|;|\.|\band\b|\bor\b|\bplus\b|\bthen\b)\s",
                    text, maxsplit=1)[0]
    text = text.strip().strip(".!?,;:")
    text = re.sub(r'^["\'“”‘’]|["\'“”‘’]$', "", text).strip()
    text = re.sub(r"^(the phrase|the line|the word|saying|the bit about)\s+", "",
                  text, flags=re.IGNORECASE).strip()
    text = re.sub(r'^["\'“”‘’]|["\'“”‘’]$', "", text).strip()
    return text


# ── The profile object ────────────────────────────────────────────────
class WritingProfile:
    """A user's learned writing style, held with confidence + decay.

    Backed by a plain JSON-safe dict (``to_dict``/``from_dict``) so persistence
    is unchanged. ``kind`` lets the same machinery serve founder / sales /
    enterprise / recruiting / personal profiles later without new storage.
    """

    def __init__(self, kind="founder", prefs=None, avoid=None, avoid_meta=None,
                 edits=0):
        self.kind = kind if kind in KINDS else "founder"
        self.prefs = prefs or {}            # key -> {confidence, count, last}
        self.avoid = list(avoid or [])      # ordered list of phrase strings
        self.avoid_meta = avoid_meta or {}  # phrase(lower) -> {confidence, last}
        self.edits = int(edits or 0)

    # ---- (de)serialisation, with v1 migration ----
    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict):
            return cls()
        if d.get("v") == 2:
            return cls(kind=d.get("kind", "founder"), prefs=d.get("prefs") or {},
                       avoid=d.get("avoid") or [], avoid_meta=d.get("avoid_meta") or {},
                       edits=d.get("edits", 0))
        # ---- migrate a v1 profile ({"prefer": {key: count}, "avoid": [str]}) ----
        now = time.time()
        prefs = {}
        for key, count in (d.get("prefer") or {}).items():
            explicit = _CUE_BY_KEY.get(key, (None, None, False))[2]
            weight = _EXPLICIT_WEIGHT if explicit else _SOFT_WEIGHT
            prefs[key] = {"confidence": min(_MAX_CONFIDENCE, weight * max(1, int(count))),
                          "count": int(count), "last": now}
        avoid = [a for a in (d.get("avoid") or []) if isinstance(a, str)]
        meta = {a.lower(): {"confidence": 0.8, "last": now} for a in avoid}
        return cls(kind=d.get("kind", "founder"), prefs=prefs, avoid=avoid,
                   avoid_meta=meta, edits=d.get("edits", 0))

    def to_dict(self):
        return {"v": 2, "kind": self.kind, "prefs": self.prefs,
                "avoid": self.avoid, "avoid_meta": self.avoid_meta,
                "edits": self.edits}

    # ---- confidence maths ----
    @staticmethod
    def _live(confidence, last, half_life_days, now):
        """Confidence after time-decay (halves every `half_life_days`)."""
        age_days = max(0.0, (now - float(last or now)) / _DAY)
        return float(confidence) * (0.5 ** (age_days / half_life_days))

    def _reinforce(self, key, weight, now):
        cur = self.prefs.get(key) or {"confidence": 0.0, "count": 0, "last": now}
        # Decay the existing confidence to "now" before adding, so stale evidence
        # doesn't stack up forever.
        live = self._live(cur["confidence"], cur.get("last", now),
                          _HALF_LIFE_DAYS, now)
        self.prefs[key] = {
            "confidence": min(_MAX_CONFIDENCE, live + weight),
            "count": int(cur.get("count", 0)) + 1,
            "last": now,
        }
        # A change of mind weakens the opposing preference.
        opp = _CONFLICTS.get(key)
        if opp and opp in self.prefs:
            om = self.prefs[opp]
            om["confidence"] = self._live(om["confidence"], om.get("last", now),
                                          _HALF_LIFE_DAYS, now) * _CONFLICT_DECAY

    def _add_avoid(self, phrase, now):
        words = phrase.split()
        if not (1 <= len(words) <= _AVOID_PHRASE_MAX_WORDS and len(phrase) >= 3):
            return False
        low = phrase.lower()
        if low not in [a.lower() for a in self.avoid]:
            self.avoid.append(phrase)
            self.avoid = self.avoid[-_AVOID_MAX:]
        meta = self.avoid_meta.get(low) or {"confidence": 0.0, "last": now}
        live = self._live(meta["confidence"], meta.get("last", now),
                          _AVOID_HALF_LIFE_DAYS, now)
        self.avoid_meta[low] = {"confidence": min(_MAX_CONFIDENCE, live + 0.8),
                                "last": now}
        # keep meta bounded to the phrases we still track
        keep = {a.lower() for a in self.avoid}
        self.avoid_meta = {k: v for k, v in self.avoid_meta.items() if k in keep}
        return True

    # ---- learning ----
    def learn(self, guidance, now=None):
        now = time.time() if now is None else now
        g = (guidance or "").strip()
        if not g:
            return self
        low = g.lower()
        learned = False
        for key, triggers, _instr, explicit in _CUES:
            if any(t in low for t in triggers):
                self._reinforce(key, _EXPLICIT_WEIGHT if explicit else _SOFT_WEIGHT, now)
                learned = True
        if any(rx.search(g) for rx in _AVOID_RES):
            hit = False
            for span in _quoted_spans(g):
                if self._add_avoid(_clean_phrase(span), now):
                    hit = True
            if not hit:
                for rx in _AVOID_RES:
                    m = rx.search(g)
                    if m and self._add_avoid(_clean_phrase(m.group(1)), now):
                        hit = True
                        break
            learned = learned or hit
        if learned:
            self.edits += 1
        return self

    # ---- rendering ----
    def active_preferences(self, now=None):
        now = time.time() if now is None else now
        out = []
        for key, _triggers, instruction, _explicit in _CUES:
            meta = self.prefs.get(key)
            if not meta:
                continue
            if self._live(meta["confidence"], meta.get("last", now),
                          _HALF_LIFE_DAYS, now) >= _ACTIVATION:
                out.append(instruction)
        return out

    def active_avoid(self, now=None):
        now = time.time() if now is None else now
        out = []
        for phrase in self.avoid:
            meta = self.avoid_meta.get(phrase.lower())
            conf = meta["confidence"] if meta else 0.8
            last = meta.get("last", now) if meta else now
            if self._live(conf, last, _AVOID_HALF_LIFE_DAYS, now) >= _AVOID_ACTIVATION:
                out.append(phrase)
        return out

    def note(self, now=None):
        prefs = self.active_preferences(now)
        avoid = self.active_avoid(now)
        if not prefs and not avoid:
            return ""
        lines = ["STANDING PREFERENCES (the user has told you these before — honor "
                 "them without being asked, and do not mention them):"]
        lines += [f"  - {p}" for p in prefs]
        if avoid:
            joined = ", ".join(f'"{a}"' for a in avoid[:_AVOID_MAX])
            lines.append(f"  - never use these phrases: {joined}")
        return "\n".join(lines)


# ── Backward-compatible module functions (dict in, dict out) ──────────
# Existing callers (chat/agent.py, chat/tools.py) and tests pass a plain dict;
# these keep that contract while using WritingProfile under the hood.
def default_profile() -> dict:
    return WritingProfile().to_dict()


def learn_from_guidance(profile: dict, guidance: str) -> dict:
    return WritingProfile.from_dict(profile).learn(guidance).to_dict()


def profile_note(profile: dict) -> str:
    return WritingProfile.from_dict(profile).note()


def active_preferences(profile: dict) -> list:
    return WritingProfile.from_dict(profile).active_preferences()
