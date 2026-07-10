"""Shared vocabulary for the guard: risk levels, decisions, and scoring helpers.

The input is a plain dict with optional sections (usage / campaign / execution /
email / personalization / sequence / mailbox / auth). Everything is optional so a
caller can guard a single email, a whole campaign, or just a budget check —
missing sections are simply not evaluated (and, where safety depends on them,
treated cautiously).
"""

# Risk bands (0-100).
LOW, MEDIUM, HIGH, CRITICAL = "LOW", "MEDIUM", "HIGH", "CRITICAL"
ALLOW, WARN, BLOCK = "ALLOW", "WARN", "BLOCK"


def risk_level(score: int) -> str:
    if score >= 80:
        return CRITICAL
    if score >= 50:
        return HIGH
    if score >= 25:
        return MEDIUM
    return LOW


def clamp(score) -> int:
    try:
        return max(0, min(100, int(round(float(score)))))
    except (TypeError, ValueError):
        return 0


class Findings:
    """Accumulates a risk score plus the issues and recommendations behind it,
    and whether a hard block was tripped. Used by both guards."""

    __slots__ = ("score", "issues", "recommendations", "block", "warn")

    def __init__(self):
        self.score = 0
        self.issues = []
        self.recommendations = []
        self.block = False
        self.warn = False

    def add(self, points, issue=None, fix=None):
        self.score += max(0, points)
        if issue and issue not in self.issues:
            self.issues.append(issue)
        if fix and fix not in self.recommendations:
            self.recommendations.append(fix)

    def block_now(self, issue, fix=None):
        self.block = True
        # A hard block implies critical risk regardless of the accumulated points.
        self.score = max(self.score, 85)
        if issue and issue not in self.issues:
            self.issues.append(issue)
        if fix and fix not in self.recommendations:
            self.recommendations.append(fix)

    def warn_now(self, issue, fix=None):
        self.warn = True
        self.add(0, issue, fix)

    def section(self) -> dict:
        return {
            "risk": risk_level(clamp(self.score)),
            "score": clamp(self.score),
            "issues": list(self.issues),
            "recommendations": list(self.recommendations),
        }


# ── Small input helpers (never raise; missing data -> conservative default) ──
def get(d, *path, default=None):
    cur = d if isinstance(d, dict) else {}
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def as_float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def as_int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def as_bool(v, default=None):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y")
    if v is None:
        return default
    return bool(v)
