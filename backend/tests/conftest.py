import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.as_posix()}"
os.environ["RUN_ESCALATION_LOOP"] = "false"
os.environ["PUBLIC_SUBMISSIONS_PER_HOUR"] = "0"  # 0 disables the limiter
os.environ["LOGIN_ATTEMPTS_PER_15MIN"] = "0"
os.environ["GOOGLE_API_KEY"] = ""  # tests never call external APIs

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.ai.chat import set_extractor  # noqa: E402
from app.ai.providers import set_providers  # noqa: E402
from app.db import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import limiter  # noqa: E402
from app.seed import seed  # noqa: E402

PASSWORD = "Test-password-123"

# Small neutral dataset so tests do not depend on the Tamil Nadu sample data.
TEST_REGIONS = {
    "Region A": {"places": ["Alpha town"], "outlets": [("001", "Outlet 001", "Alpha", 13.0850, 80.2101), ("002", "Outlet 002", "Beta", 13.0418, 80.2341), ("003", "Outlet 003", "Gamma", 12.9249, 80.1000)]},
    "Region B": {"places": ["Bravo town"], "outlets": [("004", "Outlet 004", "Delta", 11.0183, 76.9725), ("005", "Outlet 005", "Epsilon")]},
    "Region C": {"places": ["Charlie town"], "outlets": [("006", "Outlet 006", "Zeta"), ("007", "Outlet 007", "Eta")]},
}


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    init_db()
    set_providers(None)
    set_extractor(None)
    limiter.reset()
    with SessionLocal() as db:
        seed(db, PASSWORD, TEST_REGIONS)
    with TestClient(app) as c:
        yield c


def login(client, email: str) -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def admin(client):
    return login(client, "admin@tasmac.local")


def outlet_id(client, code: str) -> int:
    for r in client.get("/api/public/regions").json():
        for o in r["outlets"]:
            if o["outlet_code"] == code:
                return o["id"]
    raise AssertionError(code)


def submit(client, text: str, code: str = "001", email: str | None = None) -> dict:
    body = {"description": text, "outlet_id": outlet_id(client, code)}
    if email:
        body["contact_email"] = email
    r = client.post("/api/public/grievances", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def grievance_by_ref(client, headers, ref: str) -> dict:
    items = client.get("/api/grievances", params={"q": ref}, headers=headers).json()["items"]
    assert items, ref
    return client.get(f"/api/grievances/{items[0]['id']}", headers=headers).json()
