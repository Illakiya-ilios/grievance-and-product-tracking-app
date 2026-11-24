from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select, update

from app.db import SessionLocal
from app.import_stores import parse_rows, run
from app.models import Store, StoreStock, utcnow
from app.seed_stores import ensure_sample_stores, ensure_stock
from conftest import login

# A point in Anna Nagar, Chennai: under 1 km from shop S1, about 5 km from S2, about 18 km from S3
CHENNAI = {"lat": 13.0860, "lng": 80.2150}
SHOPS = [  # (code, name, district, lat, lng)
    ("S1", "Shop One", "Chennai", 13.0850, 80.2101),
    ("S2", "Shop Two", "Chennai", 13.0418, 80.2341),
    ("S3", "Shop Three", "Chennai", 12.9249, 80.1000),
    ("S4", "Shop Four", "Coimbatore", 11.0183, 76.9725),
]


def add_shops(client, admin):
    ids = {}
    for code, name, district, lat, lng in SHOPS:
        r = client.post("/api/admin/stores", headers=admin, json={"shop_code": code, "name": name, "district": district, "lat": lat, "lng": lng})
        assert r.status_code == 201, r.text
        ids[code] = r.json()["id"]
    return ids


def add_products(client, admin):
    ids = {}
    for name, cat, size, price in [("Lager Beer", "Beer", 650, 160), ("Blended Whisky", "Whisky", 180, 180), ("Vodka", "Vodka", 750, 780)]:
        r = client.post("/api/admin/products", headers=admin, json={"name": name, "category": cat, "size_ml": size, "price": price})
        assert r.status_code == 201, r.text
        ids[name] = r.json()["id"]
    return ids


def stock(client, admin, store_id, product_id, qty):
    r = client.put("/api/stock", headers=admin, json={"store_id": store_id, "product_id": product_id, "quantity": qty})
    assert r.status_code == 200, r.text


def nearby(client, **body):
    return client.post("/api/public/stores/nearby", json=body)


def test_nearest_shops_with_in_stock_products_sorted_by_distance(client, admin):
    s, p = add_shops(client, admin), add_products(client, admin)
    stock(client, admin, s["S1"], p["Lager Beer"], 40)
    stock(client, admin, s["S1"], p["Vodka"], 3)  # low stock
    stock(client, admin, s["S1"], p["Blended Whisky"], 0)  # out of stock: must not be listed
    stock(client, admin, s["S2"], p["Lager Beer"], 10)
    stock(client, admin, s["S3"], p["Blended Whisky"], 12)

    body = nearby(client, **CHENNAI, radius_km=50).json()
    assert [x["name"] for x in body["stores"]] == ["Shop One", "Shop Two", "Shop Three"]  # nearest first, Coimbatore is too far
    dists = [x["distance_km"] for x in body["stores"]]
    assert dists == sorted(dists) and 0 < dists[0] < dists[-1]
    first = {x["name"]: x for x in body["stores"][0]["products"]}
    assert set(first) == {"Lager Beer", "Vodka"} and first["Vodka"]["status"] == "low_stock" and first["Lager Beer"]["status"] == "in_stock"
    assert body["stores"][0]["directions_url"].startswith("https://www.google.com/maps/dir/")
    assert "Straight-line" in body["distance_note"]


def test_product_search_only_returns_shops_that_have_it(client, admin):
    s, p = add_shops(client, admin), add_products(client, admin)
    stock(client, admin, s["S1"], p["Lager Beer"], 40)
    stock(client, admin, s["S3"], p["Blended Whisky"], 12)
    r = nearby(client, **CHENNAI, radius_km=50, q="beer").json()
    assert [x["code"] for x in r["stores"]] == ["S1"] and [x["name"] for x in r["stores"][0]["products"]] == ["Lager Beer"]
    assert nearby(client, **CHENNAI, radius_km=50, q="whisky").json()["stores"][0]["code"] == "S3"
    assert nearby(client, **CHENNAI, radius_km=50, q="champagne").json()["stores"] == []


def test_radius_limit_and_nearest_outside_radius(client, admin):
    s, p = add_shops(client, admin), add_products(client, admin)
    for code in ("S1", "S2", "S3"):
        stock(client, admin, s[code], p["Lager Beer"], 20)
    assert len(nearby(client, **CHENNAI, radius_km=50, limit=2).json()["stores"]) == 2
    far = nearby(client, lat=9.93, lng=78.12, radius_km=5).json()  # Madurai: no shop within 5 km
    assert far["stores"] == [] and far["nearest_outside_radius"]["distance_km"] > 5


def test_district_fallback_when_location_is_not_shared(client, admin):
    s, p = add_shops(client, admin), add_products(client, admin)
    stock(client, admin, s["S4"], p["Vodka"], 9)
    r = nearby(client, district="coimbatore").json()  # case-insensitive
    assert r["located"] is False and [x["code"] for x in r["stores"]] == ["S4"] and r["stores"][0]["distance_km"] is None
    assert {d["name"]: d["shops"] for d in client.get("/api/public/stores/districts").json()} == {"Chennai": 3, "Coimbatore": 1}


def test_validation(client):
    assert nearby(client).status_code == 422
    assert nearby(client, lat=13.0).status_code == 422
    assert nearby(client, lat=95, lng=80).status_code == 422
    assert nearby(client, lat=13, lng=200).status_code == 422
    assert nearby(client, lat=13, lng=80, radius_km=0).status_code == 422
    assert nearby(client, district="Atlantis").status_code == 422
    assert client.get("/api/public/stores/nearby?lat=13&lng=80").status_code == 405  # location must not travel in the URL


def test_stock_and_shop_management_is_admin_only(client, admin):
    s, p = add_shops(client, admin), add_products(client, admin)
    for who in ("admin.a@tasmac.local", "officer1.a@tasmac.local"):
        h = login(client, who)
        assert client.put("/api/stock", headers=h, json={"store_id": s["S1"], "product_id": p["Vodka"], "quantity": 1}).status_code == 403
        assert client.post("/api/admin/stores", headers=h, json={"shop_code": "X", "name": "x", "district": "d", "lat": 1, "lng": 1}).status_code == 403
    assert client.put("/api/stock", json={"store_id": 1, "product_id": 1, "quantity": 1}).status_code == 401
    assert client.put("/api/stock", headers=admin, json={"store_id": s["S1"], "product_id": p["Vodka"], "quantity": -1}).status_code == 422
    assert client.put("/api/stock", headers=admin, json={"store_id": 9999, "product_id": p["Vodka"], "quantity": 1}).status_code == 404
    dup = client.post("/api/admin/stores", headers=admin, json={"shop_code": "S1", "name": "dup", "district": "Chennai", "lat": 13, "lng": 80})
    assert dup.status_code == 409


def test_stale_stock_is_flagged(client, admin):
    s, p = add_shops(client, admin), add_products(client, admin)
    stock(client, admin, s["S1"], p["Vodka"], 5)
    assert nearby(client, **CHENNAI, radius_km=50).json()["stores"][0]["stock_is_stale"] is False
    with SessionLocal() as db:
        db.execute(update(StoreStock).values(updated_at=utcnow() - timedelta(hours=30)))
        db.commit()
    assert nearby(client, **CHENNAI, radius_km=50).json()["stores"][0]["stock_is_stale"] is True


def write_csv(tmp_path: Path, text: str) -> Path:
    f = tmp_path / "shops.csv"
    f.write_text(text, encoding="utf-8-sig")
    return f


def test_import_reads_official_style_headers_and_reports_bad_rows(tmp_path):
    f = write_csv(tmp_path, (
        "Shop No,Shop Name,Address,District Name,Locality,Latitude,Longitude\n"
        "1001,TASMAC 1001,12 Main Rd,Chennai,Anna Nagar,13.0850,80.2101\n"
        "1002,,5 Bazaar St,Madurai,,9.9252,78.1198\n"
        "1002,dup,x,Madurai,,9.9,78.1\n"                # duplicate code
        "1003,No coords,x,Salem,,,\n"                    # no coordinates
        "1004,Far away,x,Salem,,28.6139,77.2090\n"       # Delhi, not Tamil Nadu
        ",no code,x,Salem,,11.6,78.1\n"                  # no shop code
        "1005,No district,x,,,11.6,78.1\n"
    ))
    rows, skipped = parse_rows(f)
    assert [r["shop_code"] for r in rows] == ["1001", "1002"]
    reasons = " | ".join(why for _, why in skipped)
    assert len(skipped) == 5 and "duplicate" in reasons and "invalid coordinates" in reasons and "outside Tamil Nadu" in reasons
    assert "no shop code" in reasons and "no district" in reasons


def test_import_requires_the_needed_columns(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="Missing required column"):
        parse_rows(write_csv(tmp_path, "shop_code,district\n1,Chennai\n"))


def test_import_replaces_sample_shops_in_search_and_generates_stock(client, tmp_path):
    with SessionLocal() as db:
        assert ensure_sample_stores(db) > 0  # the sample outlets become sample shops
        ensure_stock(db)
        sample_total = db.scalar(select(func.count()).select_from(Store))
    assert sample_total > 0
    assert nearby(client, lat=13.0850, lng=80.2101, radius_km=5).json()["stores"]  # sample shops are visible while they are all there is

    f = write_csv(tmp_path, "shop_code,name,district,lat,lng\n1001,Real One,Chennai,13.0900,80.2000\n1002,Real Two,Chennai,13.0500,80.2300\n")
    report = run(f)
    assert report["added"] == 2 and report["updated"] == 0 and report["stock_rows"] > 0 and not report["skipped"]
    again = run(f)
    assert again["added"] == 0 and again["updated"] == 2  # same file again updates, never duplicates

    names = [s["name"] for s in nearby(client, lat=13.0850, lng=80.2101, radius_km=5).json()["stores"]]
    assert names == ["Real One", "Real Two"]  # sample shops are hidden now that real ones exist
    assert {d["name"] for d in client.get("/api/public/stores/districts").json()} == {"Chennai"}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Store)) == sample_total + 2  # nothing was deleted
        assert db.scalar(select(Store.source).where(Store.shop_code == "1001")) == f"import:{f.name}"


def test_dry_run_writes_nothing(client, tmp_path):
    f = write_csv(tmp_path, "shop_code,district,lat,lng\n1001,Chennai,13.09,80.2\n")
    assert run(f, dry_run=True)["valid_rows"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Store)) == 0
