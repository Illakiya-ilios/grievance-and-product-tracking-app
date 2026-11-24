from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import current_user
from ..models import User
from ..security import client_key, create_access_token, limiter, verify_password
from ..services import ops

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Verified against when the account does not exist, so response time doesn't reveal valid emails.
_DUMMY_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEeO5nB7Q5zq5mZ5k1b4q8fJ0kqB1F1l9mW"


class LoginBody(BaseModel):
    email: str
    password: str


def user_out(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "region_id": u.region_id}


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    limiter.check(client_key(request, "login"), settings.login_attempts_per_15min, 900)
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not (user and user.active and ok):
        ops.audit(db, None, "auth.login_failed", body.email[:100])
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    ops.audit(db, user, "auth.login")
    db.commit()
    return {"access_token": create_access_token(user.id), "token_type": "bearer", "user": user_out(user)}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_out(user)
