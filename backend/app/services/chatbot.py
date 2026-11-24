"""Conversational grievance intake.

The bot asks questions until the form is complete, shows a summary, and submits only after the citizen
types an explicit confirmation. Confirmation is matched against fixed phrases in this module. No model
output can trigger a submission.
"""

import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..ai.chat import EMAIL_RE, Extraction, HeuristicExtractor, get_extractor, norm
from ..models import ChatSession, Outlet, Region, utcnow
from .intake import IntakeError, create_grievance, looks_english as _looks_english

log = logging.getLogger("tasmac.chatbot")

MAX_MESSAGE_CHARS = 1000
MAX_TURNS = 60
SESSION_TTL = timedelta(hours=24)
MAX_PROBES = 2
MIN_DESCRIPTION = 15

CONFIRM = {"yes", "y", "yes submit", "yes please", "confirm", "confirmed", "submit", "yes confirm", "yes submit it"}
DENY = {"no", "n", "nope", "change", "edit", "change something", "no change something", "no change", "not yet"}
NOTHING_MORE = {"thats all", "that s all", "that is all", "nothing more", "no more", "nothing else", "done"}
RESTART = {"start over", "restart", "new", "new complaint", "cancel", "reset", "start again"}

GREETING = (
    "Hello! I'm the TASMAC grievance assistant. I'll ask a few questions to complete your complaint, "
    "and nothing is submitted until you confirm at the end.\n\n"
    "Please describe what happened, in English."
)


@dataclass
class BotReply:
    texts: list[str]
    quick_replies: list[str] = field(default_factory=list)
    submitted: dict | None = None


def new_state() -> dict:
    return {
        "stage": "collecting",  # collecting | confirming | submitting | submitted
        "expecting": "details",
        "region_id": None,
        "outlet_id": None,
        "outlet_decided": False,
        "email": None,
        "email_decided": False,
        "details": [],
        "details_ok": False,
        "location_text": "",  # what the citizen typed about where it happened
        "probes": 0,
        "reference": None,
        "messages": [],
        "turns": 0,
    }


def catalog(db: Session) -> list[dict]:
    regions = db.scalars(select(Region).where(Region.status == "active").order_by(Region.name)).all()
    outlets = db.scalars(select(Outlet).order_by(Outlet.outlet_code)).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "places": r.places or [],
            "outlets": [
                {"id": o.id, "code": o.outlet_code, "name": o.outlet_name, "area": o.area}
                for o in outlets if o.region_id == r.id
            ],
        }
        for r in regions
    ]


def _find(cat: list[dict], region_id, outlet_id):
    region = next((r for r in cat if r["id"] == region_id), None)
    outlet = next((o for r in cat for o in r["outlets"] if o["id"] == outlet_id), None)
    return region, outlet


# ---------- session storage ----------


def load_session(db: Session, session_id: str | None) -> ChatSession | None:
    """Return the session, or None when the id is unknown or expired."""
    if not session_id:
        return None
    cs = db.get(ChatSession, session_id)
    if cs is None or utcnow() - cs.updated_at > SESSION_TTL:
        return None
    return cs


def create_session(db: Session) -> ChatSession:
    db.execute(delete(ChatSession).where(ChatSession.updated_at < utcnow() - SESSION_TTL))
    cs = ChatSession(id=secrets.token_urlsafe(24), state=new_state(), version=0)
    db.add(cs)
    db.commit()
    return cs


def save_state(db: Session, cs: ChatSession, state: dict) -> bool:
    """Compare-and-swap save. False means another request changed the session first."""
    res = db.execute(
        update(ChatSession)
        .where(ChatSession.id == cs.id, ChatSession.version == cs.version)
        .values(state=state, version=cs.version + 1, updated_at=utcnow())
    )
    db.commit()
    if res.rowcount != 1:
        return False
    cs.version += 1
    cs.state = state
    return True


# ---------- turn handling ----------


def _english_or_no_letters(text: str) -> bool:
    return not any(c.isalpha() for c in text) or _looks_english(text)


def start(db: Session) -> tuple[ChatSession, BotReply]:
    cs = create_session(db)
    state = new_state()
    state["messages"].append({"role": "bot", "text": GREETING})
    save_state(db, cs, state)
    return cs, BotReply([GREETING])


def handle_message(db: Session, cs: ChatSession, text: str, submit_allowed) -> tuple[BotReply, bool]:
    """Process one user message. Returns (reply, ok). ok=False means a concurrent request won the race."""
    state = {**new_state(), **cs.state}
    state["messages"] = list(state["messages"])
    state["details"] = list(state["details"])
    text = text.strip()[:MAX_MESSAGE_CHARS]

    state["turns"] += 1
    if state["turns"] > MAX_TURNS:
        reply = BotReply(["This conversation has gone on too long. Please say \"start over\" to begin again."], ["Start over"])
        return reply, save_state(db, cs, state)
    state["messages"].append({"role": "user", "text": text})
    n = norm(text)

    if n in RESTART and state["stage"] != "submitting":
        state = new_state()
        state["messages"].append({"role": "bot", "text": GREETING})
        return BotReply([GREETING]), save_state(db, cs, state)

    if state["stage"] == "submitted":
        reply = BotReply([f"Your grievance {state['reference']} has already been submitted. Say \"new complaint\" to file another."],
                         ["New complaint"])
    elif state["stage"] == "submitting":
        reply = BotReply(["Your grievance is being submitted. Please wait a moment."])
    elif not text:
        reply = _ask(state, db)
    elif not _english_or_no_letters(text):
        reply = BotReply(["Sorry, I can only read English text. Please write your reply in English."])
    elif state["stage"] == "confirming" and n in CONFIRM:
        return _confirm_and_submit(db, cs, state, submit_allowed)
    elif state["stage"] == "confirming" and n in DENY:
        state.update(stage="collecting", expecting="change")
        reply = BotReply(
            ["No problem. Tell me what to change: the region, the outlet, the email, or add more details about what happened. "
             "To rewrite everything, say \"start over\"."],
            ["Start over"],
        )
    else:
        reply = _collect(db, state, text)

    state["messages"].extend({"role": "bot", "text": t} for t in reply.texts)
    return reply, save_state(db, cs, state)


def _extract(db: Session, state: dict, text: str) -> Extraction:
    cat = catalog(db)
    region, outlet = _find(cat, state["region_id"], state["outlet_id"])
    draft = {
        "region": region["name"] if region else None,
        "outlet": outlet["name"] if outlet else ("none, general issue" if state["outlet_decided"] else None),
        "has_email": state["email_decided"],
        "details_so_far": state["details"],
        "location_so_far": state["location_text"],
    }
    history = state["messages"][:-1]
    try:
        return get_extractor().extract(text, history, draft, cat, state["expecting"])
    except Exception as e:  # network, quota or malformed output: keep the conversation going on keywords
        log.warning("Chat extractor failed (%s); using heuristic extractor", type(e).__name__)
        return HeuristicExtractor().extract(text, history, draft, cat, state["expecting"])


def _collect(db: Session, state: dict, text: str) -> BotReply:
    if state["expecting"] == "details" and norm(text) in NOTHING_MORE:
        state["details_ok"] = True
        return _ask(state, db)
    before = (state["region_id"], state["outlet_id"], state["outlet_decided"], state["email_decided"], len(state["details"]))
    was_confirming, was_changing = state["stage"] == "confirming", state["expecting"] == "change"
    if state["expecting"] == "location":
        state["location_text"] = f"{state['location_text']} {text}".strip()[-300:]
    ex = _extract(db, state, text)
    cat = catalog(db)
    expecting = state["expecting"]

    region, outlet = _find(cat, state["region_id"], ex.outlet_id)
    if ex.outlet_id is not None and outlet is not None:
        owner = next(r for r in cat if outlet in r["outlets"])
        state.update(outlet_id=outlet["id"], region_id=owner["id"], outlet_decided=True)
    elif ex.region_id is not None and any(r["id"] == ex.region_id for r in cat):
        if ex.region_id != state["region_id"]:
            state.update(region_id=ex.region_id, outlet_id=None, outlet_decided=False)
    if ex.no_outlet and state["region_id"] is not None:
        state.update(outlet_id=None, outlet_decided=True)

    email = ex.contact_email if ex.contact_email and EMAIL_RE.fullmatch(ex.contact_email.strip()) else None
    if email:
        state.update(email=email.strip().lower(), email_decided=True)
    elif ex.skip_email and expecting == "email":
        state.update(email=None, email_decided=True)

    if ex.adds_complaint_details:
        state["details"].append(text)
    if ex.details_sufficient:
        state["details_ok"] = True

    after = (state["region_id"], state["outlet_id"], state["outlet_decided"], state["email_decided"], len(state["details"]))
    if before == after and was_confirming:
        return BotReply(["I will only submit after you reply YES. Reply NO if you want to change something."],
                        ["Yes, submit", "No, change something"])
    if before == after and was_changing:
        state["stage"] = "collecting"
        return BotReply(["I didn\u0027t catch a change. Tell me the new region, outlet or email, or add more details."], ["Start over"])
    return _ask(state, db, ex)


def _description(state: dict) -> str:
    return "\n".join(state["details"])


def _ask(state: dict, db: Session, ex: Extraction | None = None) -> BotReply:
    cat = catalog(db)
    state["stage"] = "collecting"
    total_len = len(_description(state))
    if not state["details"] or total_len < MIN_DESCRIPTION:
        state["expecting"] = "details"
        prompt = "Please describe what happened, in a sentence or two." if not state["details"] else \
            "Could you add a little more detail about what happened?"
        return BotReply([prompt])

    if not state["details_ok"] and state["probes"] < MAX_PROBES:
        state["probes"] += 1
        state["expecting"] = "details"
        q = (ex.follow_up_question or "").strip() if ex else ""
        if not (0 < len(q) <= 200 and q.endswith("?") and "http" not in q.lower()):
            q = "Can you tell me more: what exactly happened, and roughly when?"
        return BotReply([q, "(You can say \"that's all\" if there is nothing more to add.)"], ["That's all"])

    if state["region_id"] is None:
        state["expecting"] = "location"
        if state["location_text"]:  # the citizen typed a place but it could not be matched to one region
            names = [r["name"] for r in cat]
            said = state["location_text"][:80]
            return BotReply(
                [f"I couldn't match \"{said}\" to a single area we cover. We currently cover: {', '.join(names)}. "
                 "Please tell me the city or district (and the locality, if you know it), or the outlet code."],
                names,
            )
        return BotReply(["Where did this happen? Please type the place or area, for example \"Anna Nagar, Chennai\", "
                         "or the outlet code if you know it."])

    if not state["outlet_decided"]:
        state["expecting"] = "outlet"
        region, _ = _find(cat, state["region_id"], None)
        lines = "\n".join(f"- {o['code']}: {o['name']}" for o in region["outlets"]) or "- (no outlets listed)"
        return BotReply(
            [f"Thanks. Which outlet in {region['name']} is it? Give the outlet code or locality, or say \"none\" "
             f"if you are not sure or it is a general issue.\n{lines}"],
            [o["code"] for o in region["outlets"]] + ["None"],
        )
    if not state["email_decided"]:
        state["expecting"] = "email"
        return BotReply(
            ["Optional: share an email address if you'd like us to be able to follow up. Say \"skip\" to continue without one."],
            ["Skip"],
        )

    state.update(stage="confirming", expecting="confirm")
    return BotReply([_summary(state, cat)], ["Yes, submit", "No, change something"])


def _summary(state: dict, cat: list[dict]) -> str:
    region, outlet = _find(cat, state["region_id"], state["outlet_id"])
    outlet_line = f"{outlet['name']} ({outlet['code']}), {outlet['area']}" if outlet else "General regional issue (no specific outlet)"
    said = f"- Location you typed: \"{state['location_text'][:120]}\"\n" if state["location_text"] else ""
    return (
        "Here is what I will submit:\n"
        f"- Region: {region['name']}\n"
        f"- Outlet: {outlet_line}\n"
        f"{said}"
        f"- Email: {state['email'] or 'not provided'}\n"
        f"- Your complaint: \"{_description(state)}\"\n\n"
        "Reply YES to submit this grievance, or NO to change something."
    )


def _confirm_and_submit(db: Session, cs: ChatSession, state: dict, submit_allowed) -> tuple[BotReply, bool]:
    problem = submit_allowed()  # rate limit; returns an error text or None
    if problem:
        state["messages"].append({"role": "bot", "text": problem})
        return BotReply([problem]), save_state(db, cs, state)

    # Claim the submission first so a double click cannot create two grievances.
    claimed = {**state, "stage": "submitting"}
    if not save_state(db, cs, claimed):
        return BotReply(["Your grievance is being submitted. Please wait a moment."]), False
    try:
        g, token = create_grievance(db, _description(state), state["region_id"], state["outlet_id"], state["email"])
    except IntakeError as e:
        back = {**state, "stage": "collecting", "expecting": "details"}
        msg = f"I couldn't submit that: {e}. Please correct it and we'll try again."
        back["messages"].append({"role": "bot", "text": msg})
        save_state(db, cs, back)
        return BotReply([msg]), True

    msg = (
        f"Your grievance has been submitted.\n- Reference: {g.reference}\n- Tracking code: {token}\n"
        "Please save both. The tracking code is shown only once and lets you check the status or reopen the grievance later."
    )
    done = {**claimed, "stage": "submitted", "expecting": None, "reference": g.reference}
    stored = f"Your grievance has been submitted. Reference: {g.reference}. The tracking code was shown once."
    done["messages"] = [*state["messages"], {"role": "bot", "text": stored}]
    save_state(db, cs, done)
    return BotReply([msg], ["New complaint"], {"reference": g.reference, "tracking_token": token, "grievance_id": g.id}), True
