"""Local, dependency-free providers used when no API keys are configured (and as a safety net).

They are deliberately simple: keyword rules for analysis and feature hashing for embeddings.
Every result is labelled with the "heuristic" model name so it stays auditable.
"""

import hashlib
import math
import re

from .types import AnalysisOut

SEV_RANK = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
SEV_BY_RANK = {v: k for k, v in SEV_RANK.items()}

# (pattern, category, alleged issue label, severity)
RULES: list[tuple[str, str, str, str]] = [
    (r"\brude(ly)?\b|\bmisbehav\w*|\babus\w+|\bshout\w*|\binsult\w*|\barrogan\w*", "Staff Conduct", "Rude staff behaviour", "High"),
    (r"\bharass\w*|\bmisconduct\b", "Staff Conduct", "Alleged staff misconduct", "High"),
    (r"(refus\w+|not|no|without)\b.{0,25}\b(bill|receipt)|\bno (bill|receipt)\b", "Billing Issues", "Refusal to provide bill", "High"),
    (r"additional (amount|money|charge|payment)|extra (amount|money|charge|payment|rupees)|overcharg\w*|above (the )?mrp|more than (the )?mrp|charged more", "Billing Issues", "Demand for additional payment", "High"),
    (r"\bwrong bill\w*|incorrect bill\w*|billing (error|mistake)|change (was )?not (given|returned)", "Billing Issues", "Incorrect billing", "Medium"),
    (r"\bbrib\w+|\bkickback\w*|unauthori[sz]ed payment|illegal payment|\bcommission\b", "Unethical Practices", "Alleged unauthorised payment", "High"),
    (r"\bspurious\b|\bfake\b|adulterat\w+|\bexpired\b|\btampered\b", "Unethical Practices", "Alleged sale of tampered or expired product", "Critical"),
    (r"out of stock|not available|unavailable|\bno stock\b|stock (is )?(over|finished)|ran out", "Product Availability", "Stock not available", "Medium"),
    (r"\bdirty\b|unclean\w*|\bfilth\w*|\bhygien\w*|\bsmell\w*|\bgarbage\b|broken (door|tap|light|fan|toilet|shutter|roof)|\bleak\w*|\bmaintenance\b|no (water|light|power|toilet)", "Outlet Facilities", "Poor cleanliness or maintenance", "Medium"),
    (r"long (queue|wait)|waited|\bdelay\w*|\bslow\b|poor service|\bignored\b|not responding|closed (early|before)", "Customer Service", "Poor or delayed service", "Low"),
    (r"\bthreat\w*|\bknife\b|\bassault\w*|\bviolen\w+|\battack\w*|\bweapon\w*|\bunsafe\b|\bfight\b|\btheft\b|\bstolen\b|\bfire\b", "Safety & Security", "Threat to safety or security", "Critical"),
]

ACTIONS = {
    "Staff Conduct": "Investigate staff conduct and record the staff member's response",
    "Billing Issues": "Verify billing records and sales receipts for the outlet",
    "Product Availability": "Check outlet stock levels and the supply schedule",
    "Outlet Facilities": "Inspect the outlet and schedule maintenance or cleaning",
    "Customer Service": "Review outlet operations and service timings",
    "Safety & Security": "Treat as urgent: contact the outlet and escalate to the regional administrator",
    "Unethical Practices": "Escalate for vigilance review and verify records before any conclusion",
    "Other": "Review manually and assign a category",
}

NEGATIVE = re.compile(
    r"\b(rude|refus\w*|bad|poor|dirty|worst|angry|unhappy|terrible|horrible|harass\w*|cheat\w*|threat\w*|"
    r"complain\w*|never|not|no|illegal|unsafe|disgust\w*|pathetic|demand\w*|overcharg\w*)\b",
    re.I,
)
POSITIVE = re.compile(r"\b(good|great|helpful|excellent|polite|thank\w*|satisfied|clean|appreciate\w*)\b", re.I)
OUTLET_RE = re.compile(r"outlet\s*(?:no\.?|number|#|id)?\s*[:#-]?\s*(\d+)", re.I)
LOCATION_RE = re.compile(r"\b(?:in|at|near)\s+([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+){0,2})")
_COMPILED = [(re.compile(p, re.I), c, i, s) for p, c, i, s in RULES]


class HeuristicAnalyzer:
    name = "heuristic-rules-v1"

    def analyze(self, text: str, categories: list[str], outlet_label: str | None = None) -> AnalysisOut:
        scores: dict[str, int] = {}
        issues: list[str] = []
        sev_rank = 0
        for rx, cat, issue, sev in _COMPILED:
            if rx.search(text):
                if cat not in categories:
                    continue
                scores[cat] = scores.get(cat, 0) + 1
                if issue not in issues:
                    issues.append(issue)
                sev_rank = max(sev_rank, SEV_RANK[sev])
        if scores:
            ranked = sorted(scores, key=lambda c: (-scores[c], c))
            category, related = ranked[0], ranked[1:]
            confidence = min(0.85, 0.55 + 0.1 * scores[category])
        else:
            category = "Other" if "Other" in categories else categories[0]
            related, confidence = [], 0.3

        neg, pos = len(NEGATIVE.findall(text)), len(POSITIVE.findall(text))
        sentiment = "Negative" if neg > pos else "Positive" if pos > neg else "Neutral"
        if scores and sentiment == "Neutral":
            sentiment = "Negative"

        m_outlet, m_loc = OUTLET_RE.search(text), LOCATION_RE.search(text)
        summary = " and ".join(issues[:2]).capitalize() if issues else text.strip()[:120]
        return AnalysisOut(
            complaint_summary=summary[:300],
            category=category,
            related_categories=related,
            sentiment=sentiment,
            severity=SEV_BY_RANK[sev_rank],
            entities={"outlet_id": m_outlet.group(1) if m_outlet else None, "location": m_loc.group(1) if m_loc else None},
            issues=issues,
            requires_investigation=bool(scores) and sev_rank >= 1,
            recommended_action=ACTIONS.get(category, ACTIONS["Other"]),
            confidence=confidence,
        )


STOPWORDS = frozenset(
    "a an the and or but of to in on at for with by from is are was were be been it its this that these those i we you "
    "he she they them my our your their me us as so very just also have has had do did does not no there here then".split()
)
_TOKEN = re.compile(r"[a-z0-9]+")
CONCEPT_WEIGHT = 2.5


def _bucket(token: str, dim: int) -> tuple[int, float]:
    h = hashlib.blake2b(token.encode(), digest_size=8).digest()
    n = int.from_bytes(h, "big")
    return n % dim, 1.0 if (n >> 63) & 1 else -1.0


class HashingEmbedder:
    """Feature-hashed bag of words and bigrams. Good for near-duplicates, weak on paraphrase."""

    name = "hashing-embedder-v1"
    duplicate_threshold = 0.9
    related_threshold = 0.4

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        words = [w for w in _TOKEN.findall(text.lower()) if w not in STOPWORDS and not w.isdigit()]
        feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
        vec = [0.0] * self.dim
        counts: dict[str, int] = {}
        for f in feats:
            counts[f] = counts.get(f, 0) + 1
        for f, c in counts.items():
            idx, sign = _bucket(f, self.dim)
            vec[idx] += sign * (1 + math.log(c))
        # Concept features make paraphrases of the same alleged issue land close together.
        for rx, _cat, issue, _sev in _COMPILED:
            if rx.search(text):
                idx, sign = _bucket("concept:" + issue, self.dim)
                vec[idx] += sign * CONCEPT_WEIGHT
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
