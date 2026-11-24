"""Single LangGraph workflow that analyses one grievance.

normalize -> analyze -> embed -> retrieve -> verify -> classify -> validate -> (flag_review | route_queue)

The graph only computes. Persisting results and assigning officers happens in services.processing,
so a failed run never leaves half-written rows.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import IS_POSTGRES
from ..models import Grievance, GrievanceEmbedding, Status
from ..services import ops
from .heuristics import cosine
from .providers import Providers

log = logging.getLogger("tasmac.workflow")

MAX_TEXT = 5000
CANDIDATE_POOL = 10
MIN_POOL_SIMILARITY = 0.3
SCAN_LIMIT = 2000  # SQLite fallback only; PostgreSQL uses the pgvector index


class State(TypedDict, total=False):
    grievance_id: int
    region_id: int
    outlet_id: int | None
    contact_email: str | None
    created_at: datetime
    text: str
    outlet_label: str | None
    categories: list[str]
    analysis: dict
    analysis_model: str
    analysis_error: str | None
    embedding: list[float] | None
    embedding_model: str
    embed_error: str | None
    candidates: list[dict]
    rerank_used: bool
    relations: list[dict]
    review_reasons: list[str]
    route: str


def find_candidates(db: Session, st: State, vec: list[float]) -> list[dict]:
    """Nearest neighbours inside the same region only (region isolation applies to AI too)."""
    cutoff = st["created_at"] - timedelta(days=settings.similarity_window_days)
    base_filters = [
        Grievance.region_id == st["region_id"],
        Grievance.id != st["grievance_id"],
        Grievance.created_at >= cutoff,
        Grievance.status != Status.DUPLICATE.value,
        GrievanceEmbedding.model == st["embedding_model"],  # vectors from different models are not comparable
    ]
    rows: list[tuple[Grievance, float]] = []
    if IS_POSTGRES:
        dist = GrievanceEmbedding.embedding.cosine_distance(vec)
        stmt = (
            select(Grievance, (1 - dist).label("sim"))
            .join(GrievanceEmbedding, GrievanceEmbedding.grievance_id == Grievance.id)
            .where(*base_filters)
            .order_by(dist)
            .limit(CANDIDATE_POOL)
        )
        rows = [(g, float(s)) for g, s in db.execute(stmt).all()]
    else:
        stmt = (
            select(Grievance, GrievanceEmbedding.embedding)
            .join(GrievanceEmbedding, GrievanceEmbedding.grievance_id == Grievance.id)
            .where(*base_filters)
            .order_by(Grievance.created_at.desc())
            .limit(SCAN_LIMIT)
        )
        scored = [(g, cosine(vec, emb)) for g, emb in db.execute(stmt).all()]
        scored.sort(key=lambda r: -r[1])
        rows = scored[:CANDIDATE_POOL]
    return [
        {
            "id": g.id,
            "outlet_id": g.outlet_id,
            "contact_email": g.contact_email,
            "created_at": g.created_at,
            "text": g.description,
            "sim": sim,
            "rerank": None,
        }
        for g, sim in rows
        if sim >= MIN_POOL_SIMILARITY
    ]


def build_graph(db: Session, providers: Providers):
    def normalize(st: State) -> State:
        text = re.sub(r"\s+", " ", st["text"]).strip()[:MAX_TEXT]
        return {"text": text, "review_reasons": [], "analysis_error": None, "embed_error": None}

    def analyze(st: State) -> State:
        cats = st["categories"]
        model, error = providers.analyzer.name, None
        try:
            out = providers.analyzer.analyze(st["text"], cats, st.get("outlet_label"))
        except Exception as e:  # provider/network/validation failure -> safe fallback, flagged for review
            log.warning("Analyzer failed for grievance %s: %s", st["grievance_id"], e)
            out = providers.fallback_analyzer.analyze(st["text"], cats, st.get("outlet_label"))
            out.confidence = min(out.confidence, 0.3)
            model, error = f"{providers.fallback_analyzer.name} (fallback)", f"Analyzer failed: {type(e).__name__}"
        by_lower = {c.lower(): c for c in cats}
        out.category = by_lower.get(out.category.lower(), "Other" if "Other" in cats else cats[0])
        out.related_categories = [
            by_lower[c.lower()] for c in out.related_categories if c.lower() in by_lower and by_lower[c.lower()] != out.category
        ]
        return {"analysis": out.model_dump(), "analysis_model": model, "analysis_error": error}

    def embed(st: State) -> State:
        try:
            vec = providers.embedder.embed([st["text"]])[0]
            return {"embedding": vec, "embedding_model": providers.embedder.name}
        except Exception as e:
            log.warning("Embedding failed for grievance %s: %s", st["grievance_id"], e)
            return {"embedding": None, "embedding_model": providers.embedder.name,
                    "embed_error": f"Embedding failed: {type(e).__name__}"}

    def retrieve(st: State) -> State:
        if st.get("embedding") is None:
            return {"candidates": []}
        return {"candidates": find_candidates(db, st, st["embedding"])}

    def verify(st: State) -> State:
        cands = st.get("candidates", [])
        if not cands or providers.reranker is None:
            return {"rerank_used": False}
        try:
            scores = providers.reranker.rerank(st["text"], [c["text"] for c in cands])
        except Exception as e:
            log.warning("Rerank failed for grievance %s: %s", st["grievance_id"], e)
            return {"rerank_used": False}
        for c, s in zip(cands, scores):
            c["rerank"] = s
        return {"candidates": cands, "rerank_used": True}

    def classify(st: State) -> State:
        relations = []
        for c in st.get("candidates", []):
            if st.get("rerank_used"):
                related = (c["rerank"] or 0) >= providers.rerank_threshold
            else:
                related = c["sim"] >= providers.related_threshold
            if not related:
                continue
            # Embeddings alone confuse topics that share vocabulary, so require a matching category too.
            ca = ops.current_analysis(db, c["id"])
            mine = st["analysis"]
            if ca is not None and not (
                ca.category == mine["category"]
                or ca.category in mine["related_categories"]
                or mine["category"] in (ca.related_categories or [])
            ):
                continue
            age = abs((st["created_at"] - c["created_at"]).days)
            same_outlet = st.get("outlet_id") is not None and st["outlet_id"] == c["outlet_id"]
            same_person = bool(st.get("contact_email")) and (
                (st["contact_email"] or "").lower() == (c["contact_email"] or "").lower()
            )
            is_dup = (
                same_outlet
                and age <= settings.duplicate_window_days
                and c["sim"] >= providers.duplicate_threshold
                and (same_person or c["sim"] >= 0.97)
            )
            relations.append(
                {
                    "related_id": c["id"],
                    "type": "duplicate" if is_dup else "related",
                    "score": round(c["sim"], 4),
                    "rerank_score": None if c["rerank"] is None else round(c["rerank"], 4),
                }
            )
        return {"relations": relations}

    def validate(st: State) -> State:
        reasons: list[str] = []
        a = st["analysis"]
        if st.get("analysis_error"):
            reasons.append(st["analysis_error"])
        if a["confidence"] < settings.review_confidence_threshold:
            reasons.append(f"Low AI confidence ({a['confidence']:.2f})")
        if a["category"] == "Other":
            reasons.append("Category could not be determined")
        if st.get("embed_error"):
            reasons.append(st["embed_error"])
        for r in st.get("relations", []):
            if r["type"] == "duplicate":
                reasons.append(f"Possible duplicate of grievance #{r['related_id']}")
                break
        return {"review_reasons": reasons}

    def decide(st: State) -> str:
        return "flag_review" if st["review_reasons"] else "route_queue"

    g = StateGraph(State)
    for name, fn in [
        ("normalize", normalize), ("analyze", analyze), ("embed", embed), ("retrieve", retrieve),
        ("verify", verify), ("classify", classify), ("validate", validate),
        ("flag_review", lambda st: {"route": "review"}),
        ("route_queue", lambda st: {"route": "queue"}),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "normalize")
    for a, b in [("normalize", "analyze"), ("analyze", "embed"), ("embed", "retrieve"), ("retrieve", "verify"),
                 ("verify", "classify"), ("classify", "validate")]:
        g.add_edge(a, b)
    g.add_conditional_edges("validate", decide, {"flag_review": "flag_review", "route_queue": "route_queue"})
    g.add_edge("flag_review", END)
    g.add_edge("route_queue", END)
    return g.compile()


def run_workflow(db: Session, providers: Providers, initial: State) -> State:
    return build_graph(db, providers).invoke(initial)
