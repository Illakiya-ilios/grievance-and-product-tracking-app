from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, get_scoped_grievance, get_user_or_404, is_global, require_roles, resolve_region_scope
from ..models import (
    OPEN_STATUSES,
    SEVERITIES,
    SENTIMENTS,
    Grievance,
    GrievanceAction,
    GrievanceAnalysis,
    GrievanceRelationship,
    RecurringIssue,
    Role,
    Status,
    User,
    utcnow,
)
from ..services import ops
from ..services.processing import active_categories, process_grievance
from ..services.queries import grievances_with_analysis

router = APIRouter(prefix="/api/grievances", tags=["grievances"])

ManagerUser = Depends(require_roles(Role.admin, Role.region_admin))
WorkerUser = Depends(require_roles(Role.admin, Role.region_admin, Role.officer))


def is_overdue(g: Grievance) -> bool:
    return g.status in OPEN_STATUSES and g.due_at is not None and g.due_at < utcnow()


def list_item(g: Grievance, a: GrievanceAnalysis | None) -> dict:
    return {
        "id": g.id,
        "reference": g.reference,
        "status": g.status,
        "region_id": g.region_id,
        "region_name": g.region.name,
        "outlet_id": g.outlet_id,
        "outlet_name": g.outlet.outlet_name if g.outlet else None,
        "category": a.category if a else None,
        "severity": a.severity if a else None,
        "sentiment": a.sentiment if a else None,
        "summary": a.summary if a else None,
        "confidence": a.confidence if a else None,
        "created_at": g.created_at,
        "due_at": g.due_at,
        "overdue": is_overdue(g),
        "escalated": g.escalated,
        "assigned_user_id": g.assigned_user_id,
        "assigned_user_name": g.assigned_user.name if g.assigned_user else None,
        "recurring_issue_id": g.recurring_issue_id,
        "review_reason": g.review_reason,
    }


@router.get("")
def list_grievances(
    status: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    sentiment: str | None = None,
    outlet_id: int | None = None,
    region_id: int | None = None,
    assigned_to_me: bool = False,
    overdue: bool = False,
    escalated: bool = False,
    is_open: bool = False,
    urgent: bool = False,
    days: int | None = Query(default=None, ge=1, le=365),
    recurring_issue_id: int | None = None,
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    scope = resolve_region_scope(user, region_id)
    stmt = grievances_with_analysis()
    if scope is not None:
        stmt = stmt.where(Grievance.region_id == scope)
    if status:
        stmt = stmt.where(Grievance.status == status)
    if category:
        stmt = stmt.where(GrievanceAnalysis.category == category)
    if severity:
        stmt = stmt.where(GrievanceAnalysis.severity == severity)
    if sentiment:
        stmt = stmt.where(GrievanceAnalysis.sentiment == sentiment)
    if outlet_id:
        stmt = stmt.where(Grievance.outlet_id == outlet_id)
    if assigned_to_me:
        stmt = stmt.where(Grievance.assigned_user_id == user.id)
    if overdue:
        stmt = stmt.where(Grievance.status.in_(OPEN_STATUSES), Grievance.due_at < utcnow())
    if escalated:
        stmt = stmt.where(Grievance.escalated.is_(True), Grievance.status.in_(OPEN_STATUSES))
    if is_open:
        stmt = stmt.where(Grievance.status.in_(OPEN_STATUSES))
    if urgent:
        stmt = stmt.where(Grievance.status.in_(OPEN_STATUSES), GrievanceAnalysis.severity.in_(["High", "Critical"]))
    if days:
        stmt = stmt.where(Grievance.created_at >= utcnow() - timedelta(days=days))
    if recurring_issue_id:
        stmt = stmt.where(Grievance.recurring_issue_id == recurring_issue_id)
    if q:
        stmt = stmt.where(
            or_(
                Grievance.description.icontains(q, autoescape=True),
                Grievance.reference.icontains(q, autoescape=True),
                GrievanceAnalysis.summary.icontains(q, autoescape=True),
            )
        )
    count_stmt = stmt.with_only_columns(Grievance.id, maintain_column_froms=True).order_by(None)
    total = db.scalar(select(func.count()).select_from(count_stmt.subquery()))
    rows = db.execute(stmt.order_by(Grievance.created_at.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [list_item(g, a) for g, a in rows]}


def _related(db: Session, g: Grievance) -> list[dict]:
    rels = db.scalars(
        select(GrievanceRelationship).where(
            or_(GrievanceRelationship.grievance_id == g.id, GrievanceRelationship.related_grievance_id == g.id)
        )
    ).all()
    out = []
    for r in rels:
        other_id = r.related_grievance_id if r.grievance_id == g.id else r.grievance_id
        other = db.get(Grievance, other_id)
        if other is None or other.region_id != g.region_id:  # defence in depth: never cross regions
            continue
        oa = ops.current_analysis(db, other.id)
        out.append(
            {
                "grievance_id": other.id,
                "reference": other.reference,
                "relationship_type": r.relationship_type,
                "direction": "earlier" if r.grievance_id == g.id else "later",
                "score": r.score,
                "rerank_score": r.rerank_score,
                "status": other.status,
                "summary": oa.summary if oa else None,
                "outlet_name": other.outlet.outlet_name if other.outlet else None,
                "created_at": other.created_at,
            }
        )
    return sorted(out, key=lambda x: -x["score"])


def analysis_out(a: GrievanceAnalysis) -> dict:
    return {
        "id": a.id,
        "category": a.category,
        "related_categories": a.related_categories,
        "sentiment": a.sentiment,
        "severity": a.severity,
        "summary": a.summary,
        "issues": a.issues,  # allegations by the complainant, unverified
        "entities": a.entities,
        "requires_investigation": a.requires_investigation,
        "recommended_action": a.recommended_action,
        "confidence": a.confidence,
        "source": a.source,
        "model": a.model,
        "prompt_version": a.prompt_version,
        "error": a.error,
        "created_at": a.created_at,
    }


@router.get("/{grievance_id}")
def get_grievance(grievance_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = get_scoped_grievance(db, user, grievance_id)
    analyses = db.scalars(
        select(GrievanceAnalysis).where(GrievanceAnalysis.grievance_id == g.id).order_by(GrievanceAnalysis.id.desc())
    ).all()
    actions = db.scalars(
        select(GrievanceAction).where(GrievanceAction.grievance_id == g.id).order_by(GrievanceAction.id)
    ).all()
    issue = None
    if g.recurring_issue_id:
        ri = db.get(RecurringIssue, g.recurring_issue_id)
        count = db.scalar(select(func.count()).select_from(Grievance).where(Grievance.recurring_issue_id == ri.id))
        issue = {"id": ri.id, "title": ri.title, "category": ri.category, "grievance_count": count}
    can_manage = user.role in (Role.admin.value, Role.region_admin.value)
    return {
        **list_item(g, analyses[0] if analyses else None),
        "description": g.description,  # original text, never modified
        "contact_email": g.contact_email if can_manage else None,
        "reopen_count": g.reopen_count,
        "resolved_at": g.resolved_at,
        "analysis": analysis_out(analyses[0]) if analyses else None,
        "analysis_history": [analysis_out(a) for a in analyses[1:]],
        "related": _related(db, g),
        "recurring_issue": issue,
        "actions": [
            {
                "action": a.action,
                "remarks": a.remarks,
                "performed_by": a.performed_by.name if a.performed_by else "System",
                "created_at": a.created_at,
            }
            for a in actions
        ],
        "permissions": {
            "assign": can_manage and g.status not in (Status.RESOLVED.value, Status.DUPLICATE.value),
            "review": can_manage and g.status == Status.PENDING_REVIEW.value,
            "update_status": g.status in ops.WORKING_STATUSES
            and (can_manage or g.assigned_user_id == user.id),
            "reopen": can_manage and g.status == Status.RESOLVED.value,
            "reanalyze": can_manage,
            "add_note": user.role != Role.management.value,
        },
    }


class AssignBody(BaseModel):
    user_id: int


@router.post("/{grievance_id}/assign")
def assign_grievance(
    grievance_id: int, body: AssignBody, user: User = ManagerUser, db: Session = Depends(get_db)
):
    g = get_scoped_grievance(db, user, grievance_id)
    if g.status == Status.PENDING_REVIEW.value:
        raise HTTPException(status_code=409, detail="Review the grievance before assigning it")
    ops.assign(db, g, get_user_or_404(db, body.user_id), user)
    ops.audit(db, user, "grievance.assign", f"id={g.id} to={body.user_id}")
    db.commit()
    return {"status": g.status, "assigned_user_id": g.assigned_user_id}


class StatusBody(BaseModel):
    status: Literal["IN_PROGRESS", "RESOLVED"]
    remarks: str = Field(default="", max_length=2000)


@router.post("/{grievance_id}/status")
def update_status(grievance_id: int, body: StatusBody, user: User = WorkerUser, db: Session = Depends(get_db)):
    g = get_scoped_grievance(db, user, grievance_id)
    ops.change_status(db, g, body.status, body.remarks.strip(), user)
    ops.audit(db, user, "grievance.status", f"id={g.id} status={body.status}")
    db.commit()
    return {"status": g.status}


class NoteBody(BaseModel):
    remarks: str = Field(min_length=1, max_length=2000)


@router.post("/{grievance_id}/notes", status_code=201)
def add_note(grievance_id: int, body: NoteBody, user: User = WorkerUser, db: Session = Depends(get_db)):
    g = get_scoped_grievance(db, user, grievance_id)
    if user.role == Role.officer.value and g.assigned_user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the assigned officer can annotate this grievance")
    ops.log_action(db, g, "NOTE", body.remarks.strip(), user)
    db.commit()
    return {"ok": True}


class ReviewBody(BaseModel):
    decision: Literal["approve", "duplicate"]
    category: str | None = None
    severity: Literal["Low", "Medium", "High", "Critical"] | None = None
    sentiment: Literal["Negative", "Neutral", "Positive"] | None = None
    duplicate_of_id: int | None = None
    remarks: str = Field(default="", max_length=2000)


@router.post("/{grievance_id}/review")
def review_grievance(grievance_id: int, body: ReviewBody, user: User = ManagerUser, db: Session = Depends(get_db)):
    g = get_scoped_grievance(db, user, grievance_id)
    if g.status != Status.PENDING_REVIEW.value:
        raise HTTPException(status_code=409, detail="Grievance is not awaiting review")
    current = ops.current_analysis(db, g.id)

    if body.decision == "duplicate":
        if body.duplicate_of_id is None or body.duplicate_of_id == g.id:
            raise HTTPException(status_code=422, detail="duplicate_of_id is required")
        original = get_scoped_grievance(db, user, body.duplicate_of_id)
        if original.region_id != g.region_id:
            raise HTTPException(status_code=422, detail="Duplicate must be in the same region")
        rel = db.scalar(
            select(GrievanceRelationship).where(
                GrievanceRelationship.grievance_id == g.id,
                GrievanceRelationship.related_grievance_id == original.id,
            )
        )
        if rel:
            rel.relationship_type = "duplicate"
        else:
            db.add(
                GrievanceRelationship(
                    grievance_id=g.id, related_grievance_id=original.id, relationship_type="duplicate", score=1.0
                )
            )
        g.status = Status.DUPLICATE.value
        ops.log_action(db, g, "MARKED_DUPLICATE", f"Duplicate of {original.reference}. {body.remarks}", user, public=True)
    else:
        overrides = {k: v for k, v in (("category", body.category), ("severity", body.severity), ("sentiment", body.sentiment)) if v}
        if "category" in overrides and overrides["category"] not in active_categories(db):
            raise HTTPException(status_code=422, detail="Unknown category")
        severity = current.severity if current else "Medium"
        if overrides and current:
            db.add(
                GrievanceAnalysis(
                    grievance_id=g.id,
                    category=overrides.get("category", current.category),
                    related_categories=current.related_categories,
                    sentiment=overrides.get("sentiment", current.sentiment),
                    severity=overrides.get("severity", current.severity),
                    summary=current.summary,
                    issues=current.issues,
                    entities=current.entities,
                    requires_investigation=current.requires_investigation,
                    recommended_action=current.recommended_action,
                    confidence=1.0,
                    source="human",
                    model=f"human-review:{user.id}",
                    prompt_version="n/a",
                )
            )
            severity = overrides.get("severity", current.severity)
        ops.set_due_date(db, g, severity)
        g.status = Status.OPEN.value
        g.review_reason = None
        ops.log_action(db, g, "REVIEWED", body.remarks or "Approved after review", user)
        officer = ops.pick_officer(db, g.region_id)
        if officer:
            ops.assign(db, g, officer, user)
    ops.audit(db, user, "grievance.review", f"id={g.id} decision={body.decision}")
    db.commit()
    return {"status": g.status}


class ReopenBody(BaseModel):
    remarks: str = Field(min_length=3, max_length=2000)


@router.post("/{grievance_id}/reopen")
def reopen_grievance(grievance_id: int, body: ReopenBody, user: User = ManagerUser, db: Session = Depends(get_db)):
    g = get_scoped_grievance(db, user, grievance_id)
    ops.reopen(db, g, body.remarks.strip(), user)
    ops.audit(db, user, "grievance.reopen", f"id={g.id}")
    db.commit()
    return {"status": g.status}


@router.post("/{grievance_id}/reanalyze", status_code=202)
def reanalyze(
    grievance_id: int, background: BackgroundTasks, user: User = ManagerUser, db: Session = Depends(get_db)
):
    g = get_scoped_grievance(db, user, grievance_id)
    ops.audit(db, user, "grievance.reanalyze", f"id={g.id}")
    db.commit()
    background.add_task(process_grievance, g.id, True)
    return {"accepted": True}


@router.get("/meta/filters")
def filter_options(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"categories": active_categories(db), "severities": SEVERITIES, "sentiments": SENTIMENTS,
            "statuses": [s.value for s in Status]}
