from sqlalchemy import func, select

from ..models import Grievance, GrievanceAnalysis


def latest_analysis_subquery():
    return (
        select(func.max(GrievanceAnalysis.id).label("aid"), GrievanceAnalysis.grievance_id.label("gid"))
        .group_by(GrievanceAnalysis.grievance_id)
        .subquery()
    )


def grievances_with_analysis():
    """SELECT (Grievance, current GrievanceAnalysis or None). Callers add region scope and filters."""
    latest = latest_analysis_subquery()
    return (
        select(Grievance, GrievanceAnalysis)
        .outerjoin(latest, latest.c.gid == Grievance.id)
        .outerjoin(GrievanceAnalysis, GrievanceAnalysis.id == latest.c.aid)
    )
