"""Load an official list of TASMAC shops from a CSV file.

    python -m app.import_stores shops.csv            # import
    python -m app.import_stores shops.csv --dry-run  # validate only, write nothing

Columns (header names are matched loosely, order does not matter):
    shop_code  (required)  official shop number                 also: shop_no, shop_number, code
    lat, lng   (required)  decimal degrees inside Tamil Nadu    also: latitude / longitude, lon
    district   (required)                                         also: district_name
    name       (optional)  defaults to "TASMAC Shop <code>"      also: shop_name
    address    (optional)                                         also: shop_address
    area       (optional)  locality or taluk                      also: locality, taluk, town

Rows without usable coordinates are skipped and reported, because the finder needs a position. Existing shops are
updated by shop_code, so the same file can be loaded again. Once any real shop exists, sample shops are hidden from
the public search. Synthetic stock is generated for shops that have none.
"""

import csv
import re
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal, init_db
from .models import Store, utcnow
from .seed_stores import ensure_stock

ALIASES = {
    "shop_code": {"shop_code", "shop_no", "shop_number", "shopno", "tasmac_shop_no", "shop_id", "code"},
    "name": {"name", "shop_name", "outlet_name"},
    "address": {"address", "shop_address"},
    "district": {"district", "district_name"},
    "area": {"area", "locality", "taluk", "town"},
    "lat": {"lat", "latitude"},
    "lng": {"lng", "lon", "long", "longitude"},
}
# Loose bounding box around Tamil Nadu and Puducherry's enclaves. Anything outside is almost certainly a typo.
LAT_RANGE, LNG_RANGE = (7.8, 13.7), (76.0, 80.5)


def _key(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


def parse_rows(path: Path) -> tuple[list[dict], list[tuple[int, str]]]:
    """Returns (valid rows, [(line number, reason skipped)])."""
    skipped: list[tuple[int, str]] = []
    rows: list[dict] = []
    seen: set[str] = set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("The file is empty")
        mapping: dict[str, str] = {}
        for h in reader.fieldnames:
            for field, names in ALIASES.items():
                if _key(h) in names and field not in mapping.values():
                    mapping[h] = field
        missing = {"shop_code", "lat", "lng", "district"} - set(mapping.values())
        if missing:
            raise ValueError(f"Missing required column(s): {', '.join(sorted(missing))}. Found: {', '.join(reader.fieldnames)}")
        for line, raw in enumerate(reader, start=2):
            r = {mapping[h]: (v or "").strip() for h, v in raw.items() if h in mapping}
            if not r.get("shop_code"):
                skipped.append((line, "no shop code"))
                continue
            if r["shop_code"] in seen:
                skipped.append((line, f"duplicate shop code {r['shop_code']}"))
                continue
            try:
                lat, lng = float(r["lat"]), float(r["lng"])
            except ValueError:
                skipped.append((line, f"{r['shop_code']}: missing or invalid coordinates"))
                continue
            if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LNG_RANGE[0] <= lng <= LNG_RANGE[1]):
                skipped.append((line, f"{r['shop_code']}: coordinates ({lat}, {lng}) are outside Tamil Nadu"))
                continue
            if not r.get("district"):
                skipped.append((line, f"{r['shop_code']}: no district"))
                continue
            seen.add(r["shop_code"])
            rows.append({**r, "lat": lat, "lng": lng})
    return rows, skipped


def import_rows(db: Session, rows: list[dict], source: str) -> dict:
    existing = {s.shop_code: s for s in db.scalars(select(Store))}
    added = updated = 0
    for r in rows:
        values = dict(
            name=r.get("name") or f"TASMAC Shop {r['shop_code']}", address=r.get("address", ""), district=r["district"],
            area=r.get("area", ""), lat=r["lat"], lng=r["lng"], source=source, is_sample=False, updated_at=utcnow(),
        )
        s = existing.get(r["shop_code"])
        if s is None:
            db.add(Store(shop_code=r["shop_code"], **values))
            added += 1
        else:
            for k, v in values.items():
                setattr(s, k, v)
            updated += 1
    db.commit()
    return {"added": added, "updated": updated}


def run(path: Path, dry_run: bool = False) -> dict:
    rows, skipped = parse_rows(path)
    report = {"valid_rows": len(rows), "skipped": skipped, "added": 0, "updated": 0, "stock_rows": 0}
    if dry_run or not rows:
        return report
    init_db()
    with SessionLocal() as db:
        report.update(import_rows(db, rows, f"import:{path.name}"))
        report["stock_rows"] = ensure_stock(db)
    return report


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1 or not Path(args[0]).is_file():
        sys.exit("Usage: python -m app.import_stores <shops.csv> [--dry-run]")
    try:
        result = run(Path(args[0]), dry_run="--dry-run" in sys.argv)
    except ValueError as e:
        sys.exit(f"Cannot read the file: {e}")
    print(f"Valid rows: {result['valid_rows']}  Skipped: {len(result['skipped'])}")
    for line, why in result["skipped"][:20]:
        print(f"  line {line}: {why}")
    if len(result["skipped"]) > 20:
        print(f"  ... and {len(result['skipped']) - 20} more")
    if "--dry-run" in sys.argv:
        print("Dry run: nothing was written.")
    else:
        print(f"Added {result['added']}, updated {result['updated']}, synthetic stock rows created {result['stock_rows']}.")
