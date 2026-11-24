from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Grievance, Role, User
from .security import decode_access_token

bearer = HTTPBearer(auto_error=False)

GLOBAL_ROLES = {Role.admin.value, Role.management.value}
MANAGER_ROLES = {Role.admin.value, Role.region_admin.value}


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> User:
    err = HTTPException(status_code=401, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    if creds is None:
        raise err
    uid = decode_access_token(creds.credentials)
    if uid is None:
        raise err
    user = db.get(User, uid)
    if user is None or not user.active:
        raise err
    return user


def require_roles(*roles: Role):
    allowed = {r.value for r in roles}

    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return dep


def is_global(user: User) -> bool:
    return user.role in GLOBAL_ROLES


def resolve_region_scope(user: User, requested_region_id: int | None) -> int | None:
    """Return the region id a query must be restricted to (None = all regions).

    Region-bound users are always pinned to their own region. A requested region
    that differs from theirs is rejected instead of silently ignored.
    """
    if is_global(user):
        return requested_region_id
    if user.region_id is None:
        raise HTTPException(status_code=403, detail="User is not assigned to a region")
    if requested_region_id is not None and requested_region_id != user.region_id:
        raise HTTPException(status_code=403, detail="Access to this region is not allowed")
    return user.region_id


def get_scoped_grievance(db: Session, user: User, grievance_id: int) -> Grievance:
    """Fetch a grievance enforcing region isolation. Out-of-scope rows look like 404."""
    g = db.get(Grievance, grievance_id)
    if g is None or (not is_global(user) and g.region_id != user.region_id):
        raise HTTPException(status_code=404, detail="Grievance not found")
    return g


def get_user_or_404(db: Session, user_id: int) -> User:
    u = db.scalar(select(User).where(User.id == user_id, User.active.is_(True)))
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    return u
