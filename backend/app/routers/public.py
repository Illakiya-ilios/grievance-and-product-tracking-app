from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Grievance, GrievanceAction, Outlet, Region, Status, utcnow
from ..security import client_key, limiter, new_tracking_token, tokens_match
from ..services import ops
from ..services.intake import IntakeError, create_grievance, looks_english
from ..services.processing import process_grievance

router = APIRouter(prefix="/api/public", tags=["public"])


class GrievanceCreate(BaseModel):
    description: str = Field(min_length=15, max_length=5000)
    region_id: int | None = None
    outlet_id: int | None = None
    contact_email: EmailStr | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.region_id is None and self.outlet_id is None:
            raise ValueError("Select a region or an outlet")
        if not looks_english(self.description):
            raise ValueError("Please write the grievance in English")
        return self


class ReopenBody(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)


@router.get("/regions")
def list_regions(db: Session = Depends(get_db)):
    regions = db.scalars(select(Region).where(Region.status == "active").order_by(Region.name)).all()
    outlets = db.scalars(select(Outlet).order_by(Outlet.outlet_code)).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "outlets": [
                {"id": o.id, "outlet_code": o.outlet_code, "outlet_name": o.outlet_name, "area": o.area}
                for o in outlets
                if o.region_id == r.id
            ],
        }
        for r in regions
    ]


@router.post("/grievances", status_code=201)
def submit_grievance(
    body: GrievanceCreate, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)
):
    """Direct API submission for integrations. The public web UI uses the confirmation chat bot instead."""
    limiter.check(client_key(request, "submit"), settings.public_submissions_per_hour, 3600)
    try:
        g, token = create_grievance(
            db, body.description, body.region_id, body.outlet_id, str(body.contact_email) if body.contact_email else None
        )
    except IntakeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    background.add_task(process_grievance, g.id)  # swap for an SQS message when scaling out workers
    return {"reference": g.reference, "tracking_token": token, "status": g.status}

def _get_by_token(db: Session, reference: str, token: str) -> Grievance:
    g = db.scalar(select(Grievance).where(Grievance.reference == reference))
    if g is None or not tokens_match(token, g.tracking_token_hash):
        raise HTTPException(status_code=404, detail="Grievance not found")
    return g


PUBLIC_REMARK_ACTIONS = {"RESOLVED", "REOPENED"}


@router.get("/grievances/{reference}")
def track_grievance(reference: str, request: Request, token: str = Query(min_length=8), db: Session = Depends(get_db)):
    limiter.check(client_key(request, "track"), 60, 3600)
    g = _get_by_token(db, reference, token)
    actions = db.scalars(
        select(GrievanceAction)
        .where(GrievanceAction.grievance_id == g.id, GrievanceAction.public.is_(True))
        .order_by(GrievanceAction.id)
    ).all()
    status = Status.OPEN.value if g.status in (Status.SUBMITTED.value, Status.PENDING_REVIEW.value) else g.status
    return {
        "reference": g.reference,
        "status": "UNDER_REVIEW" if g.status == Status.PENDING_REVIEW.value else status,
        "region": g.region.name,
        "outlet": g.outlet.outlet_name if g.outlet else None,
        "submitted_at": g.created_at,
        "resolved_at": g.resolved_at,
        "can_reopen": g.status == Status.RESOLVED.value
        and g.resolved_at is not None
        and utcnow() - g.resolved_at <= timedelta(days=settings.reopen_window_days),
        "timeline": [
            {"action": a.action, "remarks": a.remarks if a.action in PUBLIC_REMARK_ACTIONS else "", "at": a.created_at}
            for a in actions
        ],
    }


@router.post("/grievances/{reference}/reopen")
def reopen_grievance(
    reference: str,
    body: ReopenBody,
    request: Request,
    token: str = Query(min_length=8),
    db: Session = Depends(get_db),
):
    limiter.check(client_key(request, "reopen"), 20, 3600)
    g = _get_by_token(db, reference, token)
    if g.resolved_at is None or utcnow() - g.resolved_at > timedelta(days=settings.reopen_window_days):
        raise HTTPException(status_code=409, detail="This grievance can no longer be reopened")
    ops.reopen(db, g, body.reason.strip(), None)
    db.commit()
    return {"reference": g.reference, "status": g.status}
