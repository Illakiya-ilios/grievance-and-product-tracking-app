"""Download liquor shops in Tamil Nadu from OpenStreetMap and write them as a CSV for `app.import_stores`.

    python -m app.fetch_osm_stores            # writes data/osm_stores.csv

Source and terms: OpenStreetMap data is licensed under the ODbL. Any use must credit "OpenStreetMap contributors"
(the map and the Find stores page already do), and a public database built from it must stay under the same licence.
It makes ONE request to the public Overpass API, identifies itself, and backs off when the server is busy.
OpenStreetMap is mapped by volunteers. It is not an official TASMAC list and its coverage is incomplete.
"""

import csv
import sys
import time
from pathlib import Path

import httpx

ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter"]
HEADERS = {"User-Agent": "tasmac-grievance-app/0.1 (single bulk download for a public-interest project)"}
QUERY = """[out:json][timeout:120];
area["ISO3166-2"="IN-TN"]->.tn;
(node["shop"="alcohol"](area.tn); way["shop"="alcohol"](area.tn););
out center tags;"""
OUT = Path(__file__).resolve().parents[1] / "data" / "osm_stores.csv"


def download(max_tries: int = 5) -> dict:
    wait = 15.0
    last = "no attempt made"
    for attempt in range(1, max_tries + 1):
        for url in ENDPOINTS:
            try:
                r = httpx.post(url, data={"data": QUERY}, headers=HEADERS, timeout=180)
                if r.status_code == 200 and r.text.lstrip().startswith("{"):
                    return r.json()
                last = f"{url} answered {r.status_code}"
            except httpx.HTTPError as e:
                last = f"{url}: {type(e).__name__}"
        print(f"Attempt {attempt}/{max_tries} failed ({last}); waiting {wait:.0f}s", file=sys.stderr)
        time.sleep(wait)
        wait *= 2
    raise RuntimeError(f"Overpass is busy or unreachable: {last}")


def to_rows(data: dict) -> list[dict]:
    rows = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lng = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lng is None:
            continue
        street = " ".join(filter(None, [tags.get("addr:housenumber"), tags.get("addr:street")]))
        place = tags.get("addr:suburb") or tags.get("addr:neighbourhood") or ""
        city = tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village") or ""
        is_tasmac = "tasmac" in " ".join(str(v) for v in tags.values()).lower()
        name = (tags.get("name") or tags.get("name:en") or "").lower()
        # Keep only shops that OpenStreetMap volunteers tagged as TASMAC. Drop bars (a different licence) and untagged
        # "liquor shop" entries, some of which are private businesses or mis-tags.
        if not is_tasmac or "bar" in name.split():
            continue
        rows.append({
            "shop_code": f"osm-{el['type'][0]}{el['id']}",
            "name": tags.get("name") or tags.get("name:en") or "Liquor shop",
            "address": ", ".join(filter(None, [street, place, city, tags.get("addr:postcode", "")])),
            "district": tags.get("addr:district") or city or "",
            "area": place or city,
            "lat": lat, "lng": lng,
        })
    return rows


if __name__ == "__main__":
    data = download()
    rows = to_rows(data)
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["shop_code", "name", "address", "district", "area", "lat", "lng"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} shops to {OUT}")
    print("Next: python -m app.fill_districts   (adds a district to each shop)")
    print("Source: OpenStreetMap contributors (ODbL). Community-mapped, not an official TASMAC list.")
