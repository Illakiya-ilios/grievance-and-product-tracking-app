import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import SessionLocal, init_db
from .routers import admin, auth, chat, dashboard, grievances, public, stores
from .services import ops

log = logging.getLogger("tasmac")


def _escalate_once() -> None:
    with SessionLocal() as db:
        ops.run_escalations(db)


async def _escalation_loop() -> None:
    while True:
        await asyncio.sleep(settings.escalation_interval_minutes * 60)
        try:
            await asyncio.to_thread(_escalate_once)
        except Exception:
            log.exception("SLA escalation run failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.check_production()
    init_db()
    task = asyncio.create_task(_escalation_loop()) if settings.run_escalation_loop else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="TASMAC Grievance Analysis and Management", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type"],
)
for r in (public.router, chat.router, stores.router, auth.router, grievances.router, dashboard.router, admin.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok"}
