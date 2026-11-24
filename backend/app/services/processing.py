import logging
from collections import Counter
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..ai.providers import Providers, get_providers
from ..ai.workflow import run_workflow
from ..config import settings
from ..db import SessionLocal
from ..models import (
    Category,
    Grievance,
    GrievanceAnalysis,
    GrievanceEmbedding,
    GrievanceRelationship,
    RecurringIssue,
    Status,
    utcnow,
)
from . import ops

log = logging.getLogger("tasmac.processing")


def active_categories(db: Session) -> list[str]:
    cats = list(db.scalars(select(Category.name).where(Category.active.is_(True)).order_by(Category.id)))
    return cats or ["Other"]


def process_grievance(grievance_id: int, reanalysis: bool = False, providers: Providers | None = None) -> None:
    """Run the AI workflow for one grievance and persist the result. Never raises."""
    with SessionLocal() as db:
        try:
            _process(db, grievance_id, reanalysis, providers or get_providers())
            db.commit()
        except Exception:
            log.exception("Processing failed for grievance %s", grievance_id)
            db.rollback()
            _mark_failed(db, grievance_id, reanalysis)


def _mark_failed(db: Session, grievance_id: int, reanalysis: bool) -> None:
    try:
        g = db.get(Grievance, grievance_id)
        if g is None or reanalysis:
            return
        g.status = Status.PENDING_REVIEW.value
        g.review_reason = "Automated processing failed"
        ops.log_action(db, g, "AI_FAILED", "Automated processing failed; flagged for manual review")
        db.commit()
    except Exception:
        log.exception("Could not mark grievance %s as failed", grievance_id)
        db.rollback()


def _process(db: Session, grievance_id: int, reanalysis: bool, providers: Providers) -> None:
    g = db.get(Grievance, grievance_id)
    if g is None:
        return
    outlet_label = f"{g.outlet.outlet_name} ({g.outlet.outlet_code})" if g.outlet else None
    state = run_workflow(
        db,
        providers,
        {
            "grievance_id": g.id,
            "region_id": g.region_id,
            "outlet_id": g.outlet_id,
            "contact_email": g.contact_email,
            "created_at": g.created_at,
            "text": g.description,
            "outlet_label": outlet_label,
            "categories": active_categories(db),
        },
    )
    a = state["analysis"]
    db.add(
        GrievanceAnalysis(
            grievance_id=g.id,
            category=a["category"],
            related_categories=a["related_categories"],
            sentiment=a["sentiment"],
            severity=a["severity"],
            summary=a["complaint_summary"][:500],
            issues=a["issues"],
            entities=a["entities"],
            requires_investigation=a["requires_investigation"],
            recommended_action=a["recommended_action"][:500],
            confidence=a["confidence"],
            source="ai",
            model=state["analysis_model"][:100],
            prompt_version=settings.prompt_version,
            error=state.get("analysis_error"),
        )
    )
    if state.get("embedding") is not None:
        db.merge(
            GrievanceEmbedding(grievance_id=g.id, embedding=state["embedding"], model=state["embedding_model"][:100])
        )

    db.execute(delete(GrievanceRelationship).where(GrievanceRelationship.grievance_id == g.id))
    for r in state.get("relations", []):
        db.add(
            GrievanceRelationship(
                grievance_id=g.id,
                related_grievance_id=r["related_id"],
                relationship_type=r["type"],
                score=r["score"],
                rerank_score=r["rerank_score"],
            )
        )
    db.flush()
    attach_recurring_issue(db, g, a, [r["related_id"] for r in state.get("relations", []) if r["type"] == "related"])

    if g.status in (Status.RESOLVED.value, Status.DUPLICATE.value):
        pass
    else:
        ops.set_due_date(db, g, a["severity"])

    if reanalysis:
        ops.log_action(db, g, "REANALYZED", f"Model {state['analysis_model']}, prompt {settings.prompt_version}")
        return

    if state["route"] == "review":
        g.status = Status.PENDING_REVIEW.value
        g.review_reason = "; ".join(state["review_reasons"])[:300]
        ops.log_action(db, g, "FLAGGED_FOR_REVIEW", g.review_reason)
    else:
        g.status = Status.OPEN.value
        ops.log_action(db, g, "ANALYZED", f"Category {a['category']}, severity {a['severity']}")
        officer = ops.pick_officer(db, g.region_id)
        if officer:
            ops.assign(db, g, officer, None)


def attach_recurring_issue(db: Session, g: Grievance, analysis: dict, related_ids: list[int]) -> None:
    """Group related complaints (same region, recent) into a recurring issue once there are enough of them."""
    if not related_ids or g.recurring_issue_id is not None:
        return
    cutoff = utcnow() - timedelta(days=settings.recurring_window_days)
    members = db.scalars(
        select(Grievance).where(
            Grievance.id.in_(related_ids),
            Grievance.region_id == g.region_id,
            Grievance.created_at >= cutoff,
        )
    ).all()
    if not members:
        return
    existing = Counter(m.recurring_issue_id for m in members if m.recurring_issue_id)
    if existing:
        g.recurring_issue_id = existing.most_common(1)[0][0]
        return
    if len(members) + 1 >= settings.recurring_min_count:
        issue = RecurringIssue(region_id=g.region_id, category=analysis["category"], title=analysis["complaint_summary"][:300])
        db.add(issue)
        db.flush()
        g.recurring_issue_id = issue.id
        for m in members:
            m.recurring_issue_id = issue.id
        ops.log_action(db, g, "RECURRING_ISSUE_DETECTED", f"Recurring issue #{issue.id}: {issue.title}")
