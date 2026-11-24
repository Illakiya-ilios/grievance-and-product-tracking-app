"""Grievance workflow operations shared by the API routers and the AI processing pipeline."""

from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    OPEN_STATUSES,
    Assignment,
    AuditLog,
    Grievance,
    GrievanceAction,
    GrievanceAnalysis,
    Role,
    SlaRule,
    Status,
    User,
    utcnow,
)

DEFAULT_SLA_HOURS = {"Critical": 24, "High": 72, "Medium": 168, "Low": 336}


def audit(db: Session, user: User | None, action: str, detail: str = "") -> None:
    db.add(AuditLog(user_id=user.id if user else None, action=action, detail=detail[:500]))


def log_action(
    db: Session, g: Grievance, action: str, remarks: str = "", user: User | None = None, public: bool = False
) -> None:
    db.add(
        GrievanceAction(
            grievance_id=g.id, action=action, remarks=remarks, performed_by_id=user.id if user else None, public=public
        )
    )


def current_analysis(db: Session, grievance_id: int) -> GrievanceAnalysis | None:
    return db.scalar(
        select(GrievanceAnalysis)
        .where(GrievanceAnalysis.grievance_id == grievance_id)
        .order_by(GrievanceAnalysis.id.desc())
        .limit(1)
    )


def sla_hours(db: Session, severity: str) -> int:
    rule = db.get(SlaRule, severity)
    return rule.target_hours if rule else DEFAULT_SLA_HOURS.get(severity, 168)


def set_due_date(db: Session, g: Grievance, severity: str, start: datetime | None = None) -> None:
    g.due_at = (start or g.created_at) + timedelta(hours=sla_hours(db, severity))


def pick_officer(db: Session, region_id: int) -> User | None:
    """Least-loaded active officer in the region (None when the region has no officers)."""
    officers = db.scalars(
        select(User).where(User.region_id == region_id, User.role == Role.officer.value, User.active.is_(True))
    ).all()
    if not officers:  # no active officers: the region admin takes the case
        officers = db.scalars(
            select(User).where(User.region_id == region_id, User.role == Role.region_admin.value, User.active.is_(True))
        ).all()
    if not officers:
        return None
    loads = dict(
        db.execute(
            select(Grievance.assigned_user_id, func.count())
            .where(Grievance.assigned_user_id.in_([o.id for o in officers]), Grievance.status.in_(OPEN_STATUSES))
            .group_by(Grievance.assigned_user_id)
        ).all()
    )
    return min(officers, key=lambda o: (loads.get(o.id, 0), o.id))


def assign(db: Session, g: Grievance, assignee: User, by: User | None) -> None:
    in_region = assignee.region_id == g.region_id and assignee.role in (Role.officer.value, Role.region_admin.value)
    if not (in_region or assignee.role == Role.admin.value):  # the global admin can work any region
        raise HTTPException(status_code=422, detail="Assignee must be an officer in the grievance's region")
    if g.status in (Status.RESOLVED.value, Status.DUPLICATE.value):
        raise HTTPException(status_code=409, detail=f"Cannot assign a {g.status} grievance")
    g.assigned_user_id = assignee.id
    if g.status != Status.IN_PROGRESS.value:
        g.status = Status.ASSIGNED.value
    db.add(Assignment(grievance_id=g.id, assigned_user_id=assignee.id, assigned_by_id=by.id if by else None))
    log_action(db, g, "ASSIGNED", f"Assigned to {assignee.name}", by, public=True)


WORKING_STATUSES = {Status.ASSIGNED.value, Status.IN_PROGRESS.value, Status.REOPENED.value}


def change_status(db: Session, g: Grievance, new_status: str, remarks: str, by: User) -> None:
    if new_status not in (Status.IN_PROGRESS.value, Status.RESOLVED.value):
        raise HTTPException(status_code=422, detail="Status can only be set to IN_PROGRESS or RESOLVED")
    if g.status not in WORKING_STATUSES:
        raise HTTPException(status_code=409, detail=f"Cannot change status from {g.status}")
    if new_status == Status.RESOLVED.value and not remarks.strip():
        raise HTTPException(status_code=422, detail="Resolution remarks are required")
    if by.role == Role.officer.value and g.assigned_user_id != by.id:
        raise HTTPException(status_code=403, detail="Only the assigned officer can update this grievance")
    g.status = new_status
    if new_status == Status.RESOLVED.value:
        g.resolved_at = utcnow()
    log_action(db, g, new_status, remarks, by, public=True)


def reopen(db: Session, g: Grievance, remarks: str, by: User | None) -> None:
    if g.status != Status.RESOLVED.value:
        raise HTTPException(status_code=409, detail="Only resolved grievances can be reopened")
    g.status = Status.REOPENED.value
    g.reopen_count += 1
    g.resolved_at = None
    g.escalated = False
    analysis = current_analysis(db, g.id)
    set_due_date(db, g, analysis.severity if analysis else "Medium", start=utcnow())
    log_action(db, g, "REOPENED", remarks, by, public=True)


def run_escalations(db: Session) -> int:
    """Flag open grievances past their SLA deadline. Returns the number newly escalated."""
    now = utcnow()
    overdue = db.scalars(
        select(Grievance).where(
            Grievance.status.in_(OPEN_STATUSES),
            Grievance.due_at.is_not(None),
            Grievance.due_at < now,
            Grievance.escalated.is_(False),
        )
    ).all()
    for g in overdue:
        g.escalated = True
        log_action(db, g, "ESCALATED", "SLA deadline passed; escalated to regional administrator")
    audit(db, None, "sla.escalation_run", f"escalated={len(overdue)}")
    db.commit()
    return len(overdue)
