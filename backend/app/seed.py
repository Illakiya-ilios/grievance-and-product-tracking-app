"""Seed reference data and demo accounts.

    python -m app.seed                 # categories, SLA rules, Tamil Nadu regions and outlets, staff accounts
    python -m app.seed --demo-data     # also load ~300 historical grievances for the dashboard (no AI calls)

Passwords come from SEED_PASSWORD, or are generated and printed once.
"""

import os
import secrets
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal, init_db
from .models import Category, Outlet, Region, Role, SlaRule, User
from .security import hash_password
from .services.ops import DEFAULT_SLA_HOURS

CATEGORIES = [
    ("Staff Conduct", "Rude behaviour, misconduct"),
    ("Billing Issues", "Incorrect billing, additional charges"),
    ("Product Availability", "Stock not available"),
    ("Outlet Facilities", "Maintenance, cleanliness"),
    ("Customer Service", "Poor service, delays"),
    ("Safety & Security", "Threats, unsafe conditions"),
    ("Unethical Practices", "Alleged bribery or unauthorized payments"),
    ("Other", "Unclassified complaints"),
]

# Sample data. Regions are a logical grouping used by this application only, not TASMAC's official zones,
# and outlet codes are placeholders. Replace both with real data (POST /api/admin/regions and /api/admin/outlets).
# Format: region -> places that belong to it, and outlets as (code, name, locality).
TAMIL_NADU_REGIONS: dict[str, dict] = {
    "Chennai": {
        "places": ["Chennai", "Madras", "Tambaram", "Chromepet", "Guindy", "Porur", "Avadi", "Ambattur", "Poonamallee",
                   "Perungudi", "Tiruvallur", "Kanchipuram", "Chengalpattu"],
        "outlets": [("CHN-001", "Anna Nagar 2nd Avenue Outlet", "Anna Nagar"), ("CHN-002", "T. Nagar Pondy Bazaar Outlet", "T. Nagar"),
                    ("CHN-003", "Adyar Signal Outlet", "Adyar"), ("CHN-004", "Velachery Main Road Outlet", "Velachery"),
                    ("CHN-005", "Tambaram East Outlet", "Tambaram"), ("CHN-006", "Perambur Barracks Road Outlet", "Perambur")],
    },
    "Coimbatore": {
        "places": ["Coimbatore", "Kovai", "Tiruppur", "Erode", "Pollachi", "Mettupalayam", "Sulur", "Udhagamandalam", "Ooty",
                   "Nilgiris", "Gobichettipalayam"],
        "outlets": [("CBE-001", "Gandhipuram Cross Cut Road Outlet", "Gandhipuram"), ("CBE-002", "RS Puram Outlet", "RS Puram"),
                    ("CBE-003", "Peelamedu Outlet", "Peelamedu"), ("CBE-004", "Saibaba Colony Outlet", "Saibaba Colony"),
                    ("CBE-005", "Singanallur Outlet", "Singanallur")],
    },
    "Madurai": {
        "places": ["Madurai", "Dindigul", "Theni", "Sivaganga", "Karaikudi", "Virudhunagar", "Usilampatti", "Melur",
                   "Sivakasi", "Ramanathapuram"],
        "outlets": [("MDU-001", "Anna Nagar Outlet", "Anna Nagar"), ("MDU-002", "Simmakkal Outlet", "Simmakkal"),
                    ("MDU-003", "Thirunagar Outlet", "Thirunagar"), ("MDU-004", "KK Nagar Outlet", "KK Nagar"),
                    ("MDU-005", "Mattuthavani Bus Stand Outlet", "Mattuthavani")],
    },
    "Tiruchirappalli": {
        "places": ["Tiruchirappalli", "Trichy", "Tiruchi", "Thanjavur", "Karur", "Pudukkottai", "Perambalur", "Kumbakonam",
                   "Ariyalur", "Nagapattinam"],
        "outlets": [("TRY-001", "Srirangam Outlet", "Srirangam"), ("TRY-002", "Thillai Nagar Outlet", "Thillai Nagar"),
                    ("TRY-003", "Woraiyur Outlet", "Woraiyur"), ("TRY-004", "Cantonment Outlet", "Cantonment"),
                    ("TRY-005", "Ariyamangalam Outlet", "Ariyamangalam")],
    },
    "Salem": {
        "places": ["Salem", "Namakkal", "Dharmapuri", "Krishnagiri", "Hosur", "Attur", "Omalur", "Mettur", "Rasipuram"],
        "outlets": [("SLM-001", "Fairlands Outlet", "Fairlands"), ("SLM-002", "Hasthampatti Outlet", "Hasthampatti"),
                    ("SLM-003", "Ammapet Outlet", "Ammapet"), ("SLM-004", "Shevapet Outlet", "Shevapet")],
    },
    "Tirunelveli": {
        "places": ["Tirunelveli", "Nellai", "Thoothukudi", "Tuticorin", "Kanyakumari", "Nagercoil", "Tenkasi", "Kovilpatti"],
        "outlets": [("TNV-001", "Tirunelveli Junction Outlet", "Junction"), ("TNV-002", "Palayamkottai Outlet", "Palayamkottai"),
                    ("TNV-003", "Melapalayam Outlet", "Melapalayam"), ("TNV-004", "Vannarpettai Outlet", "Vannarpettai")],
    },
}


# Approximate centre of each outlet's locality (lat, lng). The shops themselves are placeholders, so these are not real shop addresses.
OUTLET_COORDS = {
    "CHN-001": (13.0850, 80.2101), "CHN-002": (13.0418, 80.2341), "CHN-003": (13.0012, 80.2565),
    "CHN-004": (12.9815, 80.2180), "CHN-005": (12.9249, 80.1000), "CHN-006": (13.1186, 80.2330),
    "CBE-001": (11.0183, 76.9725), "CBE-002": (11.0070, 76.9490), "CBE-003": (11.0296, 77.0229),
    "CBE-004": (11.0268, 76.9487), "CBE-005": (10.9988, 77.0270),
    "MDU-001": (9.9370, 78.1490), "MDU-002": (9.9265, 78.1230), "MDU-003": (9.8850, 78.0830),
    "MDU-004": (9.9457, 78.1420), "MDU-005": (9.9518, 78.1618),
    "TRY-001": (10.8625, 78.6930), "TRY-002": (10.8200, 78.6840), "TRY-003": (10.8140, 78.6910),
    "TRY-004": (10.8060, 78.6890), "TRY-005": (10.8010, 78.7310),
    "SLM-001": (11.6700, 78.1450), "SLM-002": (11.6620, 78.1620), "SLM-003": (11.6460, 78.1730), "SLM-004": (11.6620, 78.1500),
    "TNV-001": (8.7342, 77.6893), "TNV-002": (8.7139, 77.7357), "TNV-003": (8.6955, 77.7109), "TNV-004": (8.7276, 77.7121),
}


def region_key(name: str) -> str:
    """Short key used in seeded account emails: 'Region A' -> a, 'Chennai' -> chennai."""
    return (name.split()[-1] if name.startswith("Region ") else name.split()[0]).lower()


# Only these accounts can sign in. The other seeded accounts stay in the database (so history keeps its
# assignee names and nothing is deleted) but are disabled. Add an email here to enable it again.
ACTIVE_LOGINS = {"admin@tasmac.local", "admin.chennai@tasmac.local"}


def seed(
    db: Session,
    password: str | None = None,
    regions: dict[str, dict] | None = None,
    active_logins: set[str] | None = None,
) -> dict:
    """active_logins=None enables every account (used by the tests). Otherwise only those emails can sign in."""
    regions = regions or TAMIL_NADU_REGIONS
    password = password or os.environ.get("SEED_PASSWORD") or secrets.token_urlsafe(12)
    for name, desc in CATEGORIES:
        if not db.scalar(select(Category).where(Category.name == name)):
            db.add(Category(name=name, description=desc))
    for sev, hours in DEFAULT_SLA_HOURS.items():
        if db.get(SlaRule, sev) is None:
            db.add(SlaRule(severity=sev, target_hours=hours))
    created_regions: dict[str, Region] = {}
    for rname, spec in regions.items():
        r = db.scalar(select(Region).where(Region.name == rname)) or Region(name=rname)
        r.places = spec.get("places", [])
        db.add(r)
        db.flush()
        created_regions[rname] = r
        for code, oname, area, *own_coords in spec["outlets"]:
            lat, lng = tuple(own_coords) if own_coords else OUTLET_COORDS.get(code, (None, None))
            outlet = db.scalar(select(Outlet).where(Outlet.outlet_code == code))
            if outlet is None:
                db.add(Outlet(outlet_code=code, outlet_name=oname, area=area, region_id=r.id, lat=lat, lng=lng))
            elif outlet.lat is None and lat is not None:
                outlet.lat, outlet.lng = lat, lng

    accounts = [("Admin", "admin@tasmac.local", Role.admin, None),
                ("Management", "management@tasmac.local", Role.management, None)]
    for rname in regions:
        key = region_key(rname)
        accounts += [
            (f"{rname} Admin", f"admin.{key}@tasmac.local", Role.region_admin, created_regions[rname]),
            (f"{rname} Officer 1", f"officer1.{key}@tasmac.local", Role.officer, created_regions[rname]),
            (f"{rname} Officer 2", f"officer2.{key}@tasmac.local", Role.officer, created_regions[rname]),
        ]
    created = []
    pw_hash = hash_password(password)  # one hash is enough: all seeded accounts share this password
    for name, email, role, region in accounts:
        enabled = active_logins is None or email in active_logins
        existing = db.scalar(select(User).where(User.email == email))
        if existing is None:
            db.add(User(name=name, email=email, password_hash=pw_hash, role=role.value,
                        region_id=region.id if region else None, active=enabled))
            created.append(email)
        elif active_logins is not None:
            existing.active = enabled
    db.commit()
    return {"password": password, "created": created}


if __name__ == "__main__":
    init_db()
    with SessionLocal() as session:
        result = seed(session, active_logins=ACTIVE_LOGINS)
        if result["created"]:
            print(f"Created {len(result['created'])} accounts. Password for the enabled ones: {result['password']}")
        else:
            print("Accounts already exist; nothing created.")
        print("Enabled logins: " + ", ".join(sorted(ACTIVE_LOGINS)))
        from .seed_stores import load_sample_stock

        print(f"Loaded sample stock rows: {load_sample_stock(session)}")
        if "--demo-data" in sys.argv:
            from .seed_demo import load_demo_data

            print(f"Loaded {load_demo_data(session)} historical demo grievances.")
