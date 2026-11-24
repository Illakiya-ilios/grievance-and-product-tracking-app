"""Public "find stock near me" API.

The citizen's location is sent in a POST body, never in the URL, so it does not end up in access logs.
It is used for this one calculation and is not stored.

Scales to thousands of shops: distances are computed from the lightweight (id, lat, lng) list first, and
stock is loaded only for the few shops that are returned.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Product, Store, StoreStock, utcnow
from ..security import client_key, limiter
from ..services.geo import directions_url, haversine_km

router = APIRouter(prefix="/api/public/stores", tags=["stores"])

LOW_STOCK_AT = 5
STALE_AFTER = timedelta(hours=24)
SEARCHES_PER_HOUR = 120


class NearbyIn(BaseModel):
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    district: str | None = Field(default=None, max_length=100)  # used when the citizen does not share a location
    radius_km: float = Field(default=10, gt=0, le=100)
    limit: int = Field(default=5, ge=1, le=20)
    q: str | None = Field(default=None, max_length=60)  # product search: name, category or size

    @model_validator(mode="after")
    def _need_a_place(self):
        if (self.lat is None) != (self.lng is None):
            raise ValueError("Send both lat and lng")
        if self.lat is None and not (self.district or "").strip():
            raise ValueError("Send a location (lat and lng) or a district")
        return self


def _matches(p: Product, q: str) -> bool:
    q = q.lower()
    return q in p.name.lower() or q in p.category.lower() or q == f"{p.size_ml}ml" or q == str(p.size_ml)


def _visible_stores(db: Session):
    """Real shops only, once any exist. Sample shops are shown only while there is nothing else."""
    has_real = db.scalar(select(func.count()).select_from(Store).where(Store.is_sample.is_(False))) > 0
    return Store.is_sample.is_(False) if has_real else Store.id.is_not(None)


@router.get("/products")
def product_list(db: Session = Depends(get_db)):
    """Products and categories, for the search box."""
    rows = db.scalars(select(Product).where(Product.active.is_(True)).order_by(Product.category, Product.name, Product.size_ml)).all()
    return {
        "categories": sorted({p.category for p in rows}),
        "products": [{"id": p.id, "name": p.name, "category": p.category, "size_ml": p.size_ml, "price": p.price} for p in rows],
    }


@router.get("/districts")
def district_list(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Store.district, func.count()).where(_visible_stores(db)).group_by(Store.district).order_by(Store.district)
    ).all()
    return [{"name": d, "shops": n} for d, n in rows]


@router.post("/nearby")
def nearby(body: NearbyIn, request: Request, db: Session = Depends(get_db)):
    limiter.check(client_key(request, "stores"), SEARCHES_PER_HOUR, 3600)
    located = body.lat is not None
    where = [_visible_stores(db)]
    if body.district:
        where.append(func.lower(Store.district) == body.district.strip().lower())
        if db.scalar(select(func.count()).select_from(Store).where(*where)) == 0:
            raise HTTPException(status_code=422, detail="Unknown district")

    # 1. Rank every candidate shop by distance using only id, name and coordinates.
    rows = db.execute(select(Store.id, Store.name, Store.lat, Store.lng).where(*where)).all()
    ranked = []
    for sid, name, lat, lng in rows:
        dist = haversine_km(body.lat, body.lng, lat, lng) if located else None
        if located and dist > body.radius_km:
            continue
        ranked.append((sid, name, dist))
    ranked.sort(key=lambda r: (r[2] is None, r[2] or 0, r[1]))

    shops_in_radius = len(ranked)

    # 2. A product search keeps only the shops that have a matching product in stock.
    if body.q:
        wanted = [p.id for p in db.scalars(select(Product).where(Product.active.is_(True))) if _matches(p, body.q.strip())]
        have = set(db.scalars(
            select(StoreStock.store_id).where(StoreStock.product_id.in_(wanted or [0]), StoreStock.quantity > 0).distinct()
        ))
        ranked = [r for r in ranked if r[0] in have]
    else:
        wanted = None

    # 3. Load details and stock for the shops that will be shown.
    top = ranked[: body.limit]
    ids = [r[0] for r in top]
    dist_by_id = {r[0]: r[2] for r in top}
    stores = {s.id: s for s in db.scalars(select(Store).where(Store.id.in_(ids or [0])))}
    stock_q = (
        select(StoreStock, Product).join(Product, Product.id == StoreStock.product_id)
        .where(StoreStock.store_id.in_(ids or [0]), StoreStock.quantity > 0, Product.active.is_(True))
    )
    if wanted is not None:
        stock_q = stock_q.where(Product.id.in_(wanted or [0]))
    by_store: dict[int, list[tuple[StoreStock, Product]]] = {}
    for s, p in db.execute(stock_q).all():
        by_store.setdefault(s.store_id, []).append((s, p))

    now = utcnow()
    out = []
    for sid in ids:
        st = stores[sid]
        items = by_store.get(sid, [])
        updated = max((s.updated_at for s, _ in items), default=None)
        dist = dist_by_id[sid]
        out.append({
            "id": st.id, "code": st.shop_code, "name": st.name, "address": st.address, "area": st.area, "district": st.district,
            "lat": st.lat, "lng": st.lng,
            "distance_km": None if dist is None else round(dist, 1),
            "directions_url": directions_url(st.lat, st.lng),
            "stock_updated_at": updated,
            "stock_is_stale": updated is None or now - updated > STALE_AFTER,
            "products": [
                {"id": p.id, "name": p.name, "category": p.category, "size_ml": p.size_ml, "price": p.price,
                 "status": "low_stock" if s.quantity <= LOW_STOCK_AT else "in_stock"}
                for s, p in sorted(items, key=lambda t: (t[1].category, t[1].name, t[1].size_ml))
            ],
        })

    nearest_outside = None
    if located and shops_in_radius == 0:  # no shop at all within the radius: tell the citizen how far the closest shop is
        everything = sorted(
            ((haversine_km(body.lat, body.lng, lat, lng), sid) for sid, _n, lat, lng in rows), key=lambda t: t[0]
        )
        if everything:
            st = db.get(Store, everything[0][1])
            nearest_outside = {"name": st.name, "area": st.area, "district": st.district, "distance_km": round(everything[0][0], 1)}

    sources = set(db.scalars(select(Store.source).where(*where).distinct()))
    from_osm = any("osm" in (s or "").lower() for s in sources)
    return {
        "attribution": "Shop locations: © OpenStreetMap contributors (ODbL). Volunteer-mapped, so the list is incomplete." if from_osm else None,
        "located": located,
        "radius_km": body.radius_km,
        "stores": out,
        "total_in_radius": len(ranked),
        "nearest_outside_radius": nearest_outside,
        "distance_note": "Straight-line distance, not driving distance." if located else None,
        "notice": "Demo data: shops, locations and stock are sample records." if settings.store_data_is_sample else None,
    }
