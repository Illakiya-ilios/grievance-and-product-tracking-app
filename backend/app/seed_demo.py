"""Loads ~300 realistic historical grievances so the dashboard has something to show.

Rows are written directly (no AI calls, no embeddings) and are labelled model="seed-data" in the analysis table.
Run `python -m app.seed --demo-data` on a fresh database. Deterministic: the same data every time.
"""

import random
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai.heuristics import ACTIONS
from .models import (
    Assignment,
    Grievance,
    GrievanceAction,
    GrievanceAnalysis,
    GrievanceRelationship,
    Outlet,
    RecurringIssue,
    Region,
    Role,
    Status,
    User,
    utcnow,
)
from .security import new_tracking_token
from .services import ops

DAYS = 90

CATEGORY_WEIGHTS = {
    "Billing Issues": 22, "Staff Conduct": 20, "Product Availability": 14, "Outlet Facilities": 14,
    "Customer Service": 14, "Unethical Practices": 9, "Safety & Security": 5,
}
SEVERITY_WEIGHTS = {
    "Staff Conduct": {"Low": 10, "Medium": 35, "High": 50, "Critical": 5},
    "Billing Issues": {"Low": 10, "Medium": 30, "High": 55, "Critical": 5},
    "Product Availability": {"Low": 35, "Medium": 55, "High": 10},
    "Outlet Facilities": {"Low": 40, "Medium": 50, "High": 10},
    "Customer Service": {"Low": 50, "Medium": 40, "High": 10},
    "Safety & Security": {"Medium": 10, "High": 45, "Critical": 45},
    "Unethical Practices": {"Medium": 15, "High": 55, "Critical": 30},
}
# Outlets that attract a lot of one kind of complaint, so the dashboard shows clear hot spots.
HOT_OUTLETS = {
    "CBE-001": ("Billing Issues", 4.0), "CHN-002": ("Staff Conduct", 3.0), "MDU-002": ("Outlet Facilities", 2.5),
    "TNV-001": ("Safety & Security", 2.5), "SLM-003": ("Product Availability", 2.0), "CHN-001": ("Billing Issues", 1.8),
}
WHEN = ["yesterday evening", "last Saturday night", "this morning", "two days ago", "on Sunday afternoon",
        "last week", "around 9 pm yesterday", "on Friday evening", "today around noon"]

TEXTS = {
    "Billing Issues": [
        ("The salesman at the TASMAC outlet in {area} charged Rs. {amt} above the printed MRP and refused to give a bill {when}.",
         ["Alleged charge above MRP", "Alleged refusal to provide bill"], "Charge above MRP and no bill at {area}"),
        ("I bought a bottle at the {area} shop {when} and was billed more than the MRP. When I asked for a receipt he said there is no bill.",
         ["Alleged overcharging", "Alleged refusal to provide receipt"], "Overcharging and no receipt at {area}"),
        ("At the {area} outlet they keep asking Rs. {amt} extra on every bottle and say it is the rate now. This happened {when}.",
         ["Alleged demand for additional payment"], "Extra payment demanded at {area}"),
        ("Wrong bill given at the {area} outlet {when}. The total did not match the price of the bottles and no change was returned.",
         ["Alleged incorrect billing", "Alleged change not returned"], "Incorrect bill at {area}"),
    ],
    "Staff Conduct": [
        ("The staff at the {area} TASMAC shop shouted at me and used abusive words when I asked for the correct price {when}.",
         ["Alleged abusive behaviour"], "Abusive staff behaviour at {area}"),
        ("A salesman at the {area} outlet behaved rudely and refused to serve me {when}. Other customers saw it too.",
         ["Alleged rude behaviour", "Alleged refusal to serve"], "Rude staff and refusal to serve at {area}"),
        ("Staff at {area} outlet harassed women customers standing in the queue {when} and made comments.",
         ["Alleged harassment of customers"], "Alleged harassment at {area}"),
        ("The supervisor at the {area} shop was arrogant and told me to go elsewhere when I complained {when}.",
         ["Alleged arrogant behaviour"], "Arrogant supervisor at {area}"),
    ],
    "Product Availability": [
        ("The {area} outlet has been out of stock of the popular brands for over a week. I visited {when} again and found nothing.",
         ["Stock not available for several days"], "Prolonged stock-out at {area}"),
        ("Only expensive brands are available at the {area} shop. The regular brands are never in stock, last checked {when}.",
         ["Regular brands unavailable"], "Only costly brands stocked at {area}"),
        ("Beer is not available at the {area} outlet for many days now, the staff just say no stock {when}.",
         ["Product unavailable for many days"], "Beer unavailable at {area}"),
    ],
    "Outlet Facilities": [
        ("The area around the {area} outlet is very dirty with empty bottles and garbage piled up. I noticed it {when}.",
         ["Poor cleanliness around outlet"], "Garbage and dirt around {area} outlet"),
        ("There is no proper lighting outside the {area} shop and the shutter is damaged. It is unsafe after dark, seen {when}.",
         ["No lighting outside outlet", "Damaged shutter"], "Lighting and shutter problems at {area}"),
        ("Water is leaking inside the {area} outlet and the floor is always wet and smelly. I saw this {when}.",
         ["Leakage and unhygienic floor"], "Leakage and smell at {area}"),
        ("No fan or drinking water for the queue at the {area} outlet, people wait in the sun {when}.",
         ["No basic facilities for customers"], "No basic facilities at {area}"),
    ],
    "Customer Service": [
        ("The {area} outlet was closed before the official closing time {when} even though many people were waiting.",
         ["Alleged early closing"], "Early closing at {area}"),
        ("I waited more than 40 minutes in the queue at the {area} shop {when} because only one counter was working.",
         ["Long waiting time", "Only one counter working"], "Long queue at {area}"),
        ("Nobody at the {area} outlet answers questions about prices or return of empty bottles. Staff ignored me {when}.",
         ["Staff ignoring customers"], "Customers ignored at {area}"),
    ],
    "Unethical Practices": [
        ("The staff at the {area} shop sell bottles after closing time at higher prices and take cash without any bill. Seen {when}.",
         ["Alleged after-hours sale", "Alleged cash taken without bill"], "After-hours sales at {area}"),
        ("A salesman at the {area} outlet asked for Rs. {amt} separately to give me the brand I wanted {when}.",
         ["Alleged side payment demanded"], "Side payment demanded at {area}"),
        ("I suspect the bottles sold at the {area} outlet have been refilled. The seal looked tampered {when}.",
         ["Alleged tampered seal"], "Suspected tampered bottles at {area}"),
    ],
    "Safety & Security": [
        ("A fight broke out outside the {area} outlet {when} and the staff did nothing. It felt very unsafe for families nearby.",
         ["Fight outside outlet", "Alleged lack of security"], "Fight outside {area} outlet"),
        ("A drunk group threatened people near the {area} shop {when}. There was no security or police presence.",
         ["Threats near outlet", "No security presence"], "Threats near {area} outlet"),
        ("Someone was injured by a broken glass bottle thrown outside the {area} outlet {when}.",
         ["Injury from broken glass outside outlet"], "Injury risk outside {area} outlet"),
    ],
}


def _pick(rng: random.Random, weights: dict):
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def load_demo_data(db: Session, count: int = 300, seed: int = 42) -> int:
    rng = random.Random(seed)
    now = utcnow()
    outlets = db.scalars(select(Outlet).order_by(Outlet.outlet_code)).all()
    if not outlets or db.scalar(select(Grievance.id).limit(1)):
        return 0
    officers: dict[int, list[User]] = {}
    for u in db.scalars(select(User).where(User.role == Role.officer.value, User.active.is_(True))):
        officers.setdefault(u.region_id, []).append(u)
    region_admins = {u.region_id: u for u in db.scalars(select(User).where(User.role == Role.region_admin.value))}
    outlet_weights = [HOT_OUTLETS.get(o.outlet_code, (None, 1.0))[1] for o in outlets]

    # Spread over the window with more recent complaints, in time order so ids follow time.
    offsets = sorted((((rng.random() ** 1.25) * DAYS * 24 * 3600) for _ in range(count)), reverse=True)
    made: list[tuple[Grievance, GrievanceAnalysis]] = []
    for off in offsets:
        outlet = rng.choices(outlets, weights=outlet_weights)[0]
        hot = HOT_OUTLETS.get(outlet.outlet_code)
        category = hot[0] if hot and rng.random() < 0.5 else _pick(rng, CATEGORY_WEIGHTS)
        template, issues, summary = rng.choice(TEXTS[category])
        fmt = {"area": outlet.area, "when": rng.choice(WHEN), "amt": rng.choice([10, 20, 30, 40, 50, 100])}
        severity = _pick(rng, SEVERITY_WEIGHTS[category])
        created = now - timedelta(seconds=off)
        age_days = off / 86400

        g = Grievance(
            description=template.format(**fmt), region_id=outlet.region_id, outlet_id=outlet.id,
            tracking_token_hash=new_tracking_token()[1], created_at=created,
            contact_email=f"citizen{rng.randint(100, 999)}@example.com" if rng.random() < 0.3 else None,
        )
        db.add(g)
        db.flush()
        g.reference = f"TSM-{created.year}-{g.id:06d}"
        a = GrievanceAnalysis(
            grievance_id=g.id, category=category, related_categories=[], severity=severity,
            sentiment=rng.choices(["Negative", "Neutral", "Positive"], weights=[88, 11, 1])[0],
            summary=summary.format(**fmt)[:500], issues=issues,
            entities={"outlet_id": outlet.outlet_code, "location": outlet.area},
            requires_investigation=severity in ("High", "Critical"),
            recommended_action=ACTIONS.get(category, ""), confidence=round(rng.uniform(0.72, 0.97), 2),
            source="ai", model="seed-data", prompt_version="seed", created_at=created + timedelta(minutes=1),
        )
        db.add(a)
        _lifecycle(db, rng, g, a, now, age_days, officers.get(outlet.region_id, []), region_admins.get(outlet.region_id))
        made.append((g, a))

    _link_relationships(db, rng, made)
    _recurring_issues(db, made, now)
    db.commit()
    ops.run_escalations(db)  # flag overdue open grievances the same way the live system does
    return len(made)


def _act(db, g, action, at, remarks="", user=None, public=True):
    db.add(GrievanceAction(grievance_id=g.id, action=action, remarks=remarks,
                           performed_by_id=user.id if user else None, public=public, created_at=at))


def _lifecycle(db, rng, g, a, now, age_days, region_officers, admin) -> None:
    created = g.created_at
    target = ops.sla_hours(db, a.severity)
    g.due_at = created + timedelta(hours=target)
    _act(db, g, "SUBMITTED", created)

    roll = rng.random()
    if roll < 0.04 and age_days < 14:
        g.status = Status.PENDING_REVIEW.value
        g.review_reason = f"Low AI confidence ({rng.uniform(0.4, 0.58):.2f})"
        a.confidence = 0.5
        _act(db, g, "FLAGGED_FOR_REVIEW", created + timedelta(minutes=1), g.review_reason, public=False)
        return
    _act(db, g, "ANALYZED", created + timedelta(minutes=1), f"Category {a.category}, severity {a.severity}", public=False)
    if not region_officers:
        g.status = Status.OPEN.value
        return
    officer = rng.choice(region_officers)
    g.assigned_user_id = officer.id
    assigned_at = created + timedelta(minutes=rng.randint(2, 90))
    db.add(Assignment(grievance_id=g.id, assigned_user_id=officer.id, assigned_at=assigned_at))
    _act(db, g, "ASSIGNED", assigned_at, f"Assigned to {officer.name}", public=True)

    p_resolved = min(0.95, 0.30 + age_days / 35)
    if rng.random() < p_resolved:
        factor = rng.uniform(0.15, 0.95) if rng.random() < 0.8 else rng.uniform(1.05, 2.2)  # ~20% miss the SLA
        resolved_at = created + timedelta(hours=target * factor)
        if resolved_at < now:
            started = created + (resolved_at - created) * 0.25
            _act(db, g, "IN_PROGRESS", started, "Investigation started", officer)
            reopened = rng.random() < 0.05
            if reopened:
                first = created + (resolved_at - created) * 0.7
                _act(db, g, "RESOLVED", first, "Outlet manager counselled and billing checked.", officer)
                _act(db, g, "REOPENED", first + timedelta(days=1), "Problem happened again.")
                g.reopen_count = 1
                resolved_at = max(resolved_at, first + timedelta(days=2))
                if resolved_at >= now:
                    g.status = Status.REOPENED.value
                    return
            g.status, g.resolved_at = Status.RESOLVED.value, resolved_at
            _act(db, g, "RESOLVED", resolved_at, rng.choice([
                "Verified with outlet records and warned the staff concerned.",
                "Supervisor visited the outlet and corrected the issue.",
                "Stock replenished and the outlet manager was instructed to display price lists.",
                "Cleaning and repairs completed, photos shared with the regional office.",
            ]), officer)
            return
    r = rng.random()
    g.status = Status.IN_PROGRESS.value if r < 0.45 else Status.REOPENED.value if r < 0.5 else Status.ASSIGNED.value
    if g.status == Status.IN_PROGRESS.value:
        _act(db, g, "IN_PROGRESS", assigned_at + timedelta(hours=rng.randint(2, 30)), "Investigation started", officer)
    if g.status == Status.REOPENED.value:
        g.reopen_count = 1
        _act(db, g, "REOPENED", assigned_at + timedelta(hours=rng.randint(24, 72)), "Problem happened again.")


def _link_relationships(db: Session, rng, made) -> None:
    """Mark a few repeat submissions as duplicates and link similar complaints at the same outlet."""
    seen: dict[tuple[int, str], list[Grievance]] = {}
    for g, a in made:
        key = (g.outlet_id, a.category)
        earlier = seen.setdefault(key, [])
        if earlier and (g.created_at - earlier[-1].created_at) < timedelta(days=10):
            prev = earlier[-1]
            is_dup = rng.random() < 0.18 and g.status not in (Status.RESOLVED.value,) and (
                g.created_at - prev.created_at) < timedelta(days=3)
            db.add(GrievanceRelationship(grievance_id=g.id, related_grievance_id=prev.id,
                                         relationship_type="duplicate" if is_dup else "related",
                                         score=round(rng.uniform(0.95, 0.99) if is_dup else rng.uniform(0.85, 0.94), 3)))
            if is_dup:
                g.status = Status.DUPLICATE.value
                g.assigned_user_id = None
                g.due_at = None
                _act(db, g, "MARKED_DUPLICATE", g.created_at + timedelta(hours=3), f"Duplicate of {prev.reference}.")
        earlier.append(g)


def _recurring_issues(db: Session, made, now) -> None:
    """Group repeated complaints of one category within a region over the last 45 days."""
    cutoff = now - timedelta(days=45)
    groups: dict[tuple[int, str], list[tuple[Grievance, GrievanceAnalysis]]] = {}
    for g, a in made:
        if g.created_at >= cutoff and g.status != Status.DUPLICATE.value:
            groups.setdefault((g.region_id, a.category), []).append((g, a))
    for (region_id, category), items in groups.items():
        if len(items) < 6 or len({g.outlet_id for g, _ in items}) < 2:
            continue
        issue = RecurringIssue(region_id=region_id, category=category,
                               title=f"{category} complaints keep recurring across {db.get(Region, region_id).name} outlets", created_at=items[0][0].created_at)
        db.add(issue)
        db.flush()
        for g, _ in items:
            g.recurring_issue_id = issue.id
