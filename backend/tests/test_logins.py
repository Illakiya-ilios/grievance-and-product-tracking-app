from app.db import SessionLocal
from app.seed import seed
from conftest import PASSWORD, TEST_REGIONS, grievance_by_ref, login, submit

RUDE = "The staff at the outlet behaved rudely, refused to provide a bill and demanded an additional amount."


def two_logins_only(client):
    with SessionLocal() as db:
        seed(db, PASSWORD, TEST_REGIONS, active_logins={"admin@tasmac.local", "admin.a@tasmac.local"})


def test_only_two_accounts_can_sign_in(client):
    two_logins_only(client)
    ok = {"admin@tasmac.local", "admin.a@tasmac.local"}
    from sqlalchemy import select

    from app.models import User

    with SessionLocal() as db:
        emails = [u.email for u in db.scalars(select(User))]
    assert len(emails) == 11  # nothing was deleted
    for email in emails:
        r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert (r.status_code == 200) == (email in ok), email


def test_disabled_account_token_stops_working(client):
    officer = login(client, "officer1.a@tasmac.local")
    assert client.get("/api/auth/me", headers=officer).status_code == 200
    two_logins_only(client)
    assert client.get("/api/auth/me", headers=officer).status_code == 401


def test_cases_go_to_region_admin_and_global_admin_covers_other_regions(client):
    two_logins_only(client)
    admin_a = login(client, "admin.a@tasmac.local")
    admin = login(client, "admin@tasmac.local")

    a = grievance_by_ref(client, admin_a, submit(client, RUDE, "001")["reference"])  # Region A has no active officer
    assert a["status"] == "ASSIGNED" and a["assigned_user_name"] == "Region A Admin"

    b = grievance_by_ref(client, admin, submit(client, RUDE, "004")["reference"])  # Region B has nobody active
    assert b["status"] == "OPEN" and b["assigned_user_id"] is None
    people = client.get(f"/api/users/assignable?region_id={b['region_id']}", headers=admin).json()
    me = client.get("/api/auth/me", headers=admin).json()
    assert [p["id"] for p in people] == [me["id"]]
    assert client.post(f"/api/grievances/{b['id']}/assign", headers=admin, json={"user_id": me["id"]}).status_code == 200
    assert client.post(f"/api/grievances/{b['id']}/status", headers=admin, json={"status": "IN_PROGRESS"}).status_code == 200
    r = client.post(f"/api/grievances/{b['id']}/status", headers=admin,
                    json={"status": "RESOLVED", "remarks": "Checked with outlet records."})
    assert r.json()["status"] == "RESOLVED"
    # a region admin still cannot touch another region
    assert client.get(f"/api/grievances/{b['id']}", headers=admin_a).status_code == 404
