"""Fill the `district` column of data/osm_stores.csv by reverse geocoding each shop with OpenStreetMap's Nominatim.

    python -m app.fill_districts [path]

Follows the Nominatim usage policy: one request per second at most, a descriptive User-Agent, and only a handful of
shops (the OSM extract is small). Results are written back to the same file, so re-running skips shops that already have one.
Data is OpenStreetMap contributors (ODbL).
"""

import csv
import sys
import time
from pathlib import Path

import httpx

URL = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "tasmac-grievance-app/0.1 (one-off district lookup for a public-interest project)"}
DEFAULT = Path(__file__).resolve().parents[1] / "data" / "osm_stores.csv"


def district_of(client: httpx.Client, lat: str, lng: str) -> str:
    r = client.get(URL, params={"format": "jsonv2", "lat": lat, "lon": lng, "zoom": 10, "addressdetails": 1, "accept-language": "en"})
    r.raise_for_status()
    a = r.json().get("address", {})
    name = a.get("state_district") or a.get("county") or a.get("city") or ""
    return name.removesuffix(" District").strip()


def main(path: Path) -> None:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    todo = [r for r in rows if not r["district"]]
    print(f"{len(todo)} of {len(rows)} shops need a district")
    with httpx.Client(headers=HEADERS, timeout=30) as client:
        for i, r in enumerate(todo, 1):
            try:
                r["district"] = district_of(client, r["lat"], r["lng"])
            except httpx.HTTPError as e:
                print(f"  {r['shop_code']}: lookup failed ({type(e).__name__}); left blank")
            time.sleep(1.1)
            if i % 10 == 0:
                print(f"  {i}/{len(todo)}")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"Done. Still without a district: {sum(1 for r in rows if not r['district'])}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT)
