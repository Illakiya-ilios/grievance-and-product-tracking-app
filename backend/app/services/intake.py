"""Creates a grievance. Shared by the chat bot and the plain API endpoint."""

from sqlalchemy.orm import Session

from ..models import Grievance, Outlet, Region
from ..security import new_tracking_token
from . import ops


class IntakeError(ValueError):
    pass


def looks_english(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    non_latin = sum(1 for c in letters if ord(c) > 0x24F)
    return non_latin / len(letters) <= 0.2


def create_grievance(
    db: Session, description: str, region_id: int | None, outlet_id: int | None, contact_email: str | None
) -> tuple[Grievance, str]:
    """Insert the grievance and return it with the one-time tracking token. Commits."""
    description = description.strip()
    if not 15 <= len(description) <= 5000:
        raise IntakeError("The complaint must be between 15 and 5000 characters")
    if not looks_english(description):
        raise IntakeError("Please write the grievance in English")
    if region_id is None and outlet_id is None:
        raise IntakeError("Select a region or an outlet")
    if outlet_id is not None:
        outlet = db.get(Outlet, outlet_id)
        if outlet is None:
            raise IntakeError("Unknown outlet")
        if region_id is not None and region_id != outlet.region_id:
            raise IntakeError("Outlet does not belong to the selected region")
        region_id = outlet.region_id  # region is always derived from the outlet when one is given
    region = db.get(Region, region_id)
    if region is None or region.status != "active":
        raise IntakeError("Unknown or inactive region")

    token, token_hash = new_tracking_token()
    g = Grievance(
        description=description,
        region_id=region.id,
        outlet_id=outlet_id,
        contact_email=contact_email.lower() if contact_email else None,
        tracking_token_hash=token_hash,
    )
    db.add(g)
    db.flush()
    g.reference = f"TSM-{g.created_at.year}-{g.id:06d}"
    ops.log_action(db, g, "SUBMITTED", "", None, public=True)
    db.commit()
    return g, token
