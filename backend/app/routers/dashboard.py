import csv
import io
from collections import Counter, defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, is_global, resolve_region_scope
from ..models import OPEN_STATUSES, Grievance, Outlet, RecurringIssue, Region, Status, User, utcnow
from ..services import ops
from ..services.queries import grievances_with_analysis

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

SEV_SCORE = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}


def load_rows(db: Session, user: User, region_id: int | None, days: int):
    scope = resolve_region_scope(user, region_id)
    stmt = grievances_with_analysis().where(Grievance.created_at >= utcnow() - timedelta(days=days))
    if scope is not None:
        stmt = stmt.where(Grievance.region_id == scope)
    # Aggregation happens in Python so it behaves identically on SQLite and PostgreSQL.
    # Move to SQL GROUP BY if a window ever holds hundreds of thousands of rows.
    return scope, db.execute(stmt).all()


def metrics(rows) -> dict:
    now = utcnow()
    total = len(rows)
    open_rows = [(g, a) for g, a in rows if g.status in OPEN_STATUSES]
    resolved = [(g, a) for g, a in rows if g.resolved_at is not None and g.status == Status.RESOLVED.value]
    hours = [(g.resolved_at - g.created_at).total_seconds() / 3600 for g, _ in resolved]
    sev = [SEV_SCORE[a.severity] for _, a in rows if a]
    cats = Counter(a.category for _, a in rows if a)
    neg = sum(1 for _, a in rows if a and a.sentiment == "Negative")
    timed = [(g.resolved_at <= g.due_at) for g, _ in resolved if g.due_at]
    return {
        "total": total,
        "open": len(open_rows),
        "resolved": len(resolved),
        "pending_review": sum(1 for g, _ in rows if g.status == Status.PENDING_REVIEW.value),
        "overdue": sum(1 for g, _ in open_rows if g.due_at and g.due_at < now),
        "escalated": sum(1 for g, _ in open_rows if g.escalated),
        "duplicates": sum(1 for g, _ in rows if g.status == Status.DUPLICATE.value),
        "resolution_rate": round(len(resolved) / total, 3) if total else 0,
        "avg_resolution_hours": round(sum(hours) / len(hours), 1) if hours else None,
        "avg_severity": round(sum(sev) / len(sev), 2) if sev else None,
        "negative_share": round(neg / total, 3) if total else 0,
        "sla_compliance": round(sum(timed) / len(timed), 3) if timed else None,
        "urgent_open": sum(1 for g, a in open_rows if a and a.severity in ("High", "Critical")),
        "top_category": cats.most_common(1)[0][0] if cats else None,
    }


def _counts(items) -> list[dict]:
    return [{"name": k, "count": v} for k, v in Counter(items).most_common()]


@router.get("/summary")
def summary(
    days: int = Query(default=30, ge=1, le=365),
    region_id: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    scope, rows = load_rows(db, user, region_id, days)
    per_day = Counter(g.created_at.date().isoformat() for g, _ in rows)
    per_day_resolved = Counter(g.resolved_at.date().isoformat() for g, _ in rows if g.resolved_at)
    start = (utcnow() - timedelta(days=days - 1)).date()
    trend = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        trend.append({"date": d, "count": per_day.get(d, 0), "created": per_day.get(d, 0), "resolved": per_day_resolved.get(d, 0)})
    ri = select(func.count()).select_from(RecurringIssue).where(RecurringIssue.status == "active")
    if scope is not None:
        ri = ri.where(RecurringIssue.region_id == scope)
    return {
        "window_days": days,
        "region_id": scope,
        **metrics(rows),
        "by_category": _counts(a.category for _, a in rows if a),
        "by_severity": _counts(a.severity for _, a in rows if a),
        "by_sentiment": _counts(a.sentiment for _, a in rows if a),
        "by_status": _counts(g.status for g, _ in rows),
        "trend": trend,
        "recurring_issues": db.scalar(ri),
    }


@router.get("/regions")
def region_performance(
    days: int = Query(default=30, ge=1, le=365), user: User = Depends(current_user), db: Session = Depends(get_db)
):
    scope, rows = load_rows(db, user, None, days)
    by_region = defaultdict(list)
    for g, a in rows:
        by_region[g.region_id].append((g, a))
    regions = db.scalars(select(Region).order_by(Region.name)).all()
    return [
        {"region_id": r.id, "region_name": r.name, **metrics(by_region.get(r.id, []))}
        for r in regions
        if scope is None or r.id == scope
    ]


@router.get("/outlets")
def outlet_performance(
    days: int = Query(default=30, ge=1, le=365),
    region_id: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    scope, rows = load_rows(db, user, region_id, days)
    by_outlet = defaultdict(list)
    for g, a in rows:
        by_outlet[g.outlet_id].append((g, a))
    stmt = select(Outlet, Region.name).join(Region, Region.id == Outlet.region_id).order_by(Outlet.outlet_code)
    if scope is not None:
        stmt = stmt.where(Outlet.region_id == scope)
    out = [
        {"outlet_id": o.id, "outlet_code": o.outlet_code, "outlet_name": o.outlet_name, "area": o.area,
         "region_id": o.region_id, "region_name": rn,
         **metrics(by_outlet.get(o.id, []))}
        for o, rn in db.execute(stmt).all()
    ]
    return sorted(out, key=lambda x: -x["total"])


@router.get("/heatmap")
def heatmap(
    days: int = Query(default=30, ge=1, le=365),
    region_id: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Complaint counts per region and category, and per region and severity."""
    scope, rows = load_rows(db, user, region_id, days)
    regions = [r for r in db.scalars(select(Region).order_by(Region.name)) if scope is None or r.id == scope]
    cells = Counter((g.region_id, a.category) for g, a in rows if a)
    sev = Counter((g.region_id, a.severity) for g, a in rows if a)
    categories = [c for c, _ in Counter(a.category for _, a in rows if a).most_common()]
    return {
        "regions": [{"id": r.id, "name": r.name} for r in regions],
        "categories": categories,
        "cells": [{"region_id": r.id, "category": c, "count": cells.get((r.id, c), 0)} for r in regions for c in categories],
        "severity": [{"region_id": r.id, "region": r.name, **{s: sev.get((r.id, s), 0) for s in ("Low", "Medium", "High", "Critical")}}
                     for r in regions],
    }


@router.get("/recurring")
def recurring_issues(
    region_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    scope = resolve_region_scope(user, region_id)
    stmt = select(RecurringIssue, Region.name).join(Region, Region.id == RecurringIssue.region_id)
    if scope is not None:
        stmt = stmt.where(RecurringIssue.region_id == scope)
    out = []
    for ri, region_name in db.execute(stmt).all():
        members = db.scalars(select(Grievance).where(Grievance.recurring_issue_id == ri.id)).all()
        out.append(
            {
                "id": ri.id,
                "title": ri.title,
                "category": ri.category,
                "region_id": ri.region_id,
                "region_name": region_name,
                "status": ri.status,
                "grievance_count": len(members),
                "open_count": sum(1 for m in members if m.status in OPEN_STATUSES),
                "outlet_count": len({m.outlet_id for m in members if m.outlet_id}),
                "first_seen": min(m.created_at for m in members) if members else ri.created_at,
                "last_seen": max(m.created_at for m in members) if members else ri.created_at,
            }
        )
    return sorted(out, key=lambda x: (-x["open_count"], -x["grievance_count"]))


@router.get("/insights")
def insights(
    days: int = Query(default=30, ge=1, le=365),
    region_id: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    scope, rows = load_rows(db, user, region_id, days)
    names = {r.id: r.name for r in db.scalars(select(Region))}
    outlet_names = {o.id: o.outlet_name for o in db.scalars(select(Outlet))}
    out: list[str] = []
    by_region = defaultdict(list)
    for g, a in rows:
        by_region[g.region_id].append((g, a))
    for rid, items in by_region.items():
        cats = Counter(a.category for _, a in items if a)
        if len(items) >= 5 and cats:
            cat, n = cats.most_common(1)[0]
            if n / len(items) >= 0.3:
                out.append(f"{names[rid]}: {round(100 * n / len(items))}% of {len(items)} complaints concern {cat}.")
    per_outlet = Counter(g.outlet_id for g, _ in rows if g.outlet_id)
    if len(per_outlet) >= 2:
        avg = sum(per_outlet.values()) / len(per_outlet)
        for oid, n in per_outlet.most_common(3):
            if n >= 3 and n >= 2 * avg:
                out.append(f"{outlet_names[oid]} has {n} complaints, {n / avg:.1f}x the average outlet.")
    m = metrics(rows)
    if m["overdue"]:
        n = m["overdue"]
        out.append(f"{n} open complaint{' is' if n == 1 else 's are'} past the SLA deadline.")
    urgent = sum(1 for g, a in rows if a and a.severity in ("High", "Critical") and g.status in OPEN_STATUSES)
    if urgent:
        out.append(f"{urgent} High or Critical complaints are still open.")
    if m["pending_review"]:
        n = m["pending_review"]
        out.append(f"{n} complaint{' is' if n == 1 else 's are'} waiting for human review.")
    half = utcnow() - timedelta(days=days / 2)
    recent = sum(1 for g, _ in rows if g.created_at >= half)
    earlier = len(rows) - recent
    if recent >= 5 and earlier and recent >= 1.5 * earlier:
        out.append(f"Complaint volume rose from {earlier} to {recent} between the first and second half of the period.")
    for ri in recurring_issues(scope, user, db)[:3]:
        if ri["open_count"]:
            out.append(
                f"Recurring issue in {ri['region_name']}: \"{ri['title']}\" "
                f"({ri['grievance_count']} complaints across {ri['outlet_count']} outlets)."
            )
    return {"window_days": days, "insights": out}


def _csv_safe(value) -> str:
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@router.get("/export.csv")
def export_csv(
    days: int = Query(default=30, ge=1, le=365),
    region_id: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    scope, rows = load_rows(db, user, region_id, days)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["reference", "region", "outlet", "status", "category", "severity", "sentiment", "summary",
                "created_at", "resolved_at", "due_at", "escalated"])
    for g, a in rows:
        w.writerow([_csv_safe(v) for v in [
            g.reference, g.region.name, g.outlet.outlet_name if g.outlet else "", g.status,
            a.category if a else "", a.severity if a else "", a.sentiment if a else "", a.summary if a else "",
            g.created_at.isoformat(), g.resolved_at.isoformat() if g.resolved_at else "",
            g.due_at.isoformat() if g.due_at else "", g.escalated]])
    ops.audit(db, user, "report.export", f"days={days} region={scope}")
    db.commit()
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=grievances.csv"})
