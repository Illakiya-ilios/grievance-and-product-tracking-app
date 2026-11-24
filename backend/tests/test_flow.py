import pytest
from sqlalchemy import select

from app.ai.heuristics import HashingEmbedder, HeuristicAnalyzer
from app.ai.providers import Providers, set_providers
from app.db import SessionLocal
from app.models import Grievance
from app.services import ops
from conftest import grievance_by_ref, login, outlet_id, submit

RUDE = (
    "The staff at TASMAC outlet 001 in Anna Nagar behaved rudely. They refused to provide a proper bill "
    "and demanded an additional amount."
)


def test_submission_is_analysed_and_routed(client, admin):
    sub = submit(client, RUDE)
    assert sub["status"] == "SUBMITTED" and sub["tracking_token"]
    g = grievance_by_ref(client, admin, sub["reference"])
    a = g["analysis"]
    assert a["category"] in ("Staff Conduct", "Billing Issues")
    assert "Staff Conduct" in [a["category"], *a["related_categories"]]
    assert a["severity"] == "High" and a["sentiment"] == "Negative"
    assert a["entities"]["outlet_id"] == "001"
    assert a["model"] and a["prompt_version"]
    assert g["status"] == "ASSIGNED" and g["assigned_user_name"]
    assert g["due_at"] is not None
    assert g["description"] == RUDE


def test_region_isolation_is_enforced_by_backend(client):
    a_admin = login(client, "admin.a@tasmac.local")
    b_admin = login(client, "admin.b@tasmac.local")
    sub = submit(client, RUDE, "001")  # Region A
    gid = grievance_by_ref(client, a_admin, sub["reference"])["id"]

    assert client.get(f"/api/grievances/{gid}", headers=b_admin).status_code == 404
    assert client.post(f"/api/grievances/{gid}/status", headers=b_admin,
                       json={"status": "IN_PROGRESS"}).status_code == 404
    assert client.get("/api/grievances", headers=b_admin).json()["total"] == 0
    assert client.get("/api/grievances", params={"region_id": 1}, headers=b_admin).status_code == 403
    assert client.get("/api/dashboard/summary", params={"region_id": 1}, headers=b_admin).status_code == 403
    assert client.get("/api/grievances", headers=a_admin).json()["total"] == 1


def test_outlet_determines_region_and_mismatch_rejected(client):
    r = client.post("/api/public/grievances",
                    json={"description": RUDE, "outlet_id": outlet_id(client, "001"), "region_id": 2})
    assert r.status_code == 422
    r = client.post("/api/public/grievances", json={"description": RUDE})
    assert r.status_code == 422


def test_english_only_and_validation(client):
    r = client.post("/api/public/grievances",
                    json={"description": "இந்த கடையில் ஊழியர்கள் மோசமாக நடந்து கொண்டனர்", "region_id": 1})
    assert r.status_code == 422
    assert client.post("/api/public/grievances", json={"description": "short", "region_id": 1}).status_code == 422


def test_general_regional_complaint_without_outlet(client, admin):
    r = client.post("/api/public/grievances",
                    json={"description": "Supply of stock to the region is irregular, brands often not available.",
                          "region_id": 2})
    assert r.status_code == 201
    g = grievance_by_ref(client, admin, r.json()["reference"])
    assert g["outlet_id"] is None and g["region_id"] == 2


def test_duplicate_detected_and_sent_to_review(client):
    admin_a = login(client, "admin.a@tasmac.local")
    first = submit(client, RUDE, "001", "citizen@example.com")
    second = submit(client, RUDE, "001", "citizen@example.com")
    g1 = grievance_by_ref(client, admin_a, first["reference"])
    g2 = grievance_by_ref(client, admin_a, second["reference"])
    assert g2["status"] == "PENDING_REVIEW"
    assert any(r["relationship_type"] == "duplicate" and r["grievance_id"] == g1["id"] for r in g2["related"])
    assert any(r["direction"] == "later" for r in g1["related"])

    r = client.post(f"/api/grievances/{g2['id']}/review", headers=admin_a,
                    json={"decision": "duplicate", "duplicate_of_id": g1["id"]})
    assert r.status_code == 200 and r.json()["status"] == "DUPLICATE"


def test_related_not_duplicate_for_different_people_and_recurring_issue(client):
    admin_a = login(client, "admin.a@tasmac.local")
    texts = [
        "Staff at outlet 001 demanded an additional amount above MRP and gave no bill.",
        "The outlet 002 charged extra money above MRP and refused a bill.",
        "Outlet 003 staff asked for extra money over MRP and would not give a bill.",
    ]
    refs = [submit(client, t, code, f"u{i}@example.com")["reference"] for i, (t, code) in enumerate(zip(texts, ["001", "002", "003"]))]
    last = grievance_by_ref(client, admin_a, refs[-1])
    assert last["recurring_issue"] is not None
    assert last["recurring_issue"]["grievance_count"] == 3
    assert all(r["relationship_type"] == "related" for r in last["related"])
    issues = client.get("/api/dashboard/recurring", headers=admin_a).json()
    assert issues[0]["outlet_count"] == 3


def test_complaints_in_other_regions_are_never_matched(client):
    submit(client, RUDE, "001")  # Region A
    admin_b = login(client, "admin.b@tasmac.local")
    sub = submit(client, RUDE, "004")  # Region B, identical text
    g = grievance_by_ref(client, admin_b, sub["reference"])
    assert g["related"] == [] and g["status"] != "PENDING_REVIEW"


def test_resolution_reopen_and_officer_permissions(client):
    admin_a = login(client, "admin.a@tasmac.local")
    sub = submit(client, RUDE)
    g = grievance_by_ref(client, admin_a, sub["reference"])
    officer_email = next(
        e for e in ("officer1.a@tasmac.local", "officer2.a@tasmac.local")
        if login(client, e) and g["assigned_user_name"].endswith(e[7])
    )
    officer = login(client, officer_email)
    other = login(client, "officer2.a@tasmac.local" if officer_email.startswith("officer1") else "officer1.a@tasmac.local")

    assert client.post(f"/api/grievances/{g['id']}/status", headers=other, json={"status": "IN_PROGRESS"}).status_code == 403
    assert client.post(f"/api/grievances/{g['id']}/status", headers=officer, json={"status": "IN_PROGRESS"}).status_code == 200
    assert client.post(f"/api/grievances/{g['id']}/status", headers=officer, json={"status": "RESOLVED"}).status_code == 422
    r = client.post(f"/api/grievances/{g['id']}/status", headers=officer,
                    json={"status": "RESOLVED", "remarks": "Outlet manager warned; billing verified."})
    assert r.json()["status"] == "RESOLVED"

    track = client.get(f"/api/public/grievances/{sub['reference']}", params={"token": sub["tracking_token"]}).json()
    assert track["status"] == "RESOLVED" and track["can_reopen"]
    assert "officer" not in str(track["timeline"]).lower()
    assert client.get(f"/api/public/grievances/{sub['reference']}", params={"token": "wrong-token-value"}).status_code == 404

    r = client.post(f"/api/public/grievances/{sub['reference']}/reopen",
                    params={"token": sub["tracking_token"]}, json={"reason": "Problem happened again"})
    assert r.json()["status"] == "REOPENED"
    final = client.get(f"/api/grievances/{g['id']}", headers=admin_a).json()
    assert final["reopen_count"] == 1 and final["resolved_at"] is None
    assert [a["action"] for a in final["actions"]][-1] == "REOPENED"


def test_management_is_read_only_and_sees_all_regions(client):
    mgmt = login(client, "management@tasmac.local")
    sub = submit(client, RUDE)
    admin = login(client, "admin@tasmac.local")
    gid = grievance_by_ref(client, admin, sub["reference"])["id"]
    assert client.get(f"/api/grievances/{gid}", headers=mgmt).status_code == 200
    assert client.post(f"/api/grievances/{gid}/reopen", headers=mgmt, json={"remarks": "x y z"}).status_code == 403
    regions = client.get("/api/dashboard/regions", headers=mgmt).json()
    assert len(regions) == 3 and sum(r["total"] for r in regions) == 1
    assert len(client.get("/api/dashboard/outlets", headers=mgmt).json()) == 7


def test_analyzer_failure_falls_back_and_flags_review(client, admin):
    class Boom:
        name = "boom-model"

        def analyze(self, *a, **k):
            raise RuntimeError("provider down")

    set_providers(Providers(Boom(), HashingEmbedder(1024)))
    sub = submit(client, RUDE)
    g = grievance_by_ref(client, admin, sub["reference"])
    assert g["status"] == "PENDING_REVIEW"
    assert "fallback" in g["analysis"]["model"] and g["analysis"]["confidence"] <= 0.3
    assert "Analyzer failed" in g["review_reason"]


def test_human_review_override_creates_new_analysis_version(client):
    set_providers(Providers(HeuristicAnalyzer(), HashingEmbedder(1024)))
    admin_a = login(client, "admin.a@tasmac.local")
    sub = submit(client, "Something odd happened at the outlet yesterday evening and I want it checked.")
    g = grievance_by_ref(client, admin_a, sub["reference"])
    assert g["status"] == "PENDING_REVIEW" and g["analysis"]["category"] == "Other"
    r = client.post(f"/api/grievances/{g['id']}/review", headers=admin_a,
                    json={"decision": "approve", "category": "Customer Service", "severity": "Medium"})
    assert r.status_code == 200
    g = client.get(f"/api/grievances/{g['id']}", headers=admin_a).json()
    assert g["analysis"]["source"] == "human" and g["analysis"]["category"] == "Customer Service"
    assert len(g["analysis_history"]) == 1 and g["status"] == "ASSIGNED"
    assert client.post(f"/api/grievances/{g['id']}/review", headers=admin_a,
                       json={"decision": "approve", "category": "Nope"}).status_code == 409


def test_original_description_is_immutable(client):
    submit(client, RUDE)
    with SessionLocal() as db:
        g = db.scalar(select(Grievance))
        g.description = "tampered"
        with pytest.raises(ValueError):
            db.commit()


def test_sla_escalation(client, admin):
    from datetime import timedelta

    sub = submit(client, RUDE)
    with SessionLocal() as db:
        g = db.scalar(select(Grievance))
        g.due_at = g.created_at - timedelta(hours=1)
        db.commit()
        assert ops.run_escalations(db) == 1
        assert ops.run_escalations(db) == 0
    g = grievance_by_ref(client, admin, sub["reference"])
    assert g["escalated"] and g["overdue"]
    assert client.get("/api/grievances", params={"escalated": True}, headers=admin).json()["total"] == 1


def test_dashboard_summary_insights_and_csv_safety(client):
    admin_a = login(client, "admin.a@tasmac.local")
    submit(client, "=cmd|' /C calc'!A0 outlet 001 was dirty and the toilet has no water.")
    s = client.get("/api/dashboard/summary", headers=admin_a).json()
    assert s["total"] == 1 and s["by_category"][0]["name"] == "Outlet Facilities" and len(s["trend"]) == 30
    assert client.get("/api/dashboard/insights", headers=admin_a).status_code == 200
    csv_text = client.get("/api/dashboard/export.csv", headers=admin_a).text
    assert "\n=" not in csv_text and "\r=" not in csv_text


def test_auth_required_and_bad_login(client):
    assert client.get("/api/grievances").status_code == 401
    assert client.post("/api/auth/login", json={"email": "admin@tasmac.local", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "ghost@x.com", "password": "nope"}).status_code == 401
    assert client.get("/api/admin/categories", headers=login(client, "admin.a@tasmac.local")).status_code == 403
