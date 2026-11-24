from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..security import client_key, limiter
from ..services import chatbot
from ..services.processing import process_grievance

router = APIRouter(prefix="/api/public/chat", tags=["chat"])

CHAT_MESSAGES_PER_HOUR = 120


class ChatIn(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    message: str | None = Field(default=None, max_length=chatbot.MAX_MESSAGE_CHARS)


def _out(cs, reply: chatbot.BotReply) -> dict:
    return {
        "session_id": cs.id,
        "stage": cs.state.get("stage"),
        "replies": reply.texts,
        "quick_replies": reply.quick_replies,
        "submitted": None if reply.submitted is None else {
            "reference": reply.submitted["reference"], "tracking_token": reply.submitted["tracking_token"]},
    }


@router.post("")
def chat(body: ChatIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """One conversation turn. Send no session_id (and no message) to start a new chat."""
    limiter.check(client_key(request, "chat"), CHAT_MESSAGES_PER_HOUR, 3600)
    if body.session_id is None:
        cs, reply = chatbot.start(db)
        return _out(cs, reply)

    cs = chatbot.load_session(db, body.session_id)
    if cs is None:
        raise HTTPException(status_code=404, detail="This chat has expired. Please start a new one.")

    def submit_allowed() -> str | None:
        try:
            limiter.check(client_key(request, "submit"), settings.public_submissions_per_hour, 3600)
        except HTTPException:
            return "Too many grievances were submitted from your connection recently. Please try again later."
        return None

    reply, ok = chatbot.handle_message(db, cs, body.message or "", submit_allowed)
    if not ok:
        raise HTTPException(status_code=409, detail="Your previous message is still being processed. Please try again.")
    if reply.submitted:
        background.add_task(process_grievance, reply.submitted["grievance_id"])
    db.refresh(cs)
    return _out(cs, reply)


@router.get("/{session_id}")
def transcript(session_id: str, db: Session = Depends(get_db)):
    """Restore the visible conversation after a page reload. The tracking code is never included."""
    cs = chatbot.load_session(db, session_id)
    if cs is None:
        raise HTTPException(status_code=404, detail="This chat has expired. Please start a new one.")
    return {"session_id": cs.id, "stage": cs.state.get("stage"), "messages": cs.state.get("messages", [])}
