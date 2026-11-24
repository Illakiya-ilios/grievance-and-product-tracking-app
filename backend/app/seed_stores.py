"""Sample products and per-shop stock for the "find stock near me" feature.

Product names, prices and stock quantities are invented. Shops come either from the sample grievance outlets
(is_sample=True) or from an official list loaded with `python -m app.import_stores`. Stock is generated for any shop
that has none yet, so imported shops get synthetic stock until a real inventory feed replaces it (PUT /api/stock).
"""

import random
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Outlet, Product, Region, Store, StoreStock, utcnow

# (name, category, size_ml, price in INR)
PRODUCTS = [
    ("Blended Whisky", "Whisky", 180, 180), ("Blended Whisky", "Whisky", 375, 360), ("Blended Whisky", "Whisky", 750, 720),
    ("Premium Whisky", "Whisky", 750, 1450),
    ("Brandy", "Brandy", 180, 170), ("Brandy", "Brandy", 375, 330), ("Brandy", "Brandy", 750, 650),
    ("Dark Rum", "Rum", 180, 150), ("Dark Rum", "Rum", 375, 300), ("Dark Rum", "Rum", 750, 600),
    ("Vodka", "Vodka", 750, 780), ("Gin", "Gin", 750, 720),
    ("Lager Beer", "Beer", 650, 160), ("Strong Beer", "Beer", 650, 170), ("Light Beer", "Beer", 500, 150),
    ("Red Wine", "Wine", 750, 550),
]


def ensure_products(db: Session) -> list[Product]:
    if db.scalar(select(Product.id).limit(1)) is None:
        db.add_all([Product(name=n, category=c, size_ml=s, price=float(p)) for n, c, s, p in PRODUCTS])
        db.flush()
    return list(db.scalars(select(Product).where(Product.active.is_(True))))


def ensure_sample_stores(db: Session) -> int:
    """Turn the sample grievance outlets into sample shops, only when there are no shops at all."""
    if db.scalar(select(Store.id).limit(1)) is not None:
        return 0
    rows = db.execute(
        select(Outlet, Region.name).join(Region, Region.id == Outlet.region_id).where(Outlet.lat.is_not(None))
    ).all()
    for o, region in rows:
        db.add(Store(shop_code=o.outlet_code, name=o.outlet_name, address=f"{o.area}, {region}", district=region, area=o.area,
                     lat=o.lat, lng=o.lng, source="sample", is_sample=True, outlet_id=o.id))
    db.flush()
    return len(rows)


def ensure_stock(db: Session, seed: int = 7) -> int:
    """Generate synthetic stock for every shop that has no stock rows yet. Returns rows written."""
    products = ensure_products(db)
    have = set(db.scalars(select(StoreStock.store_id).distinct()))
    rng = random.Random(seed)
    now = utcnow()
    rows = 0
    for sid in db.scalars(select(Store.id).order_by(Store.id)):
        if sid in have:
            continue
        stale = rng.random() < 0.1  # a few shops have not updated their stock for a day or two
        for p in products:
            if rng.random() > 0.78:
                continue  # this shop does not carry the product
            qty = rng.choices([0, rng.randint(1, 5), rng.randint(6, 60)], weights=[10, 15, 75])[0]
            age = timedelta(hours=rng.randint(26, 60)) if stale else timedelta(minutes=rng.randint(5, 360))
            db.add(StoreStock(store_id=sid, product_id=p.id, quantity=qty, updated_at=now - age))
            rows += 1
    db.commit()
    return rows


def load_sample_stock(db: Session) -> int:
    ensure_sample_stores(db)
    n = ensure_stock(db)
    return n


def counts(db: Session) -> dict:
    return {
        "shops": db.scalar(select(func.count()).select_from(Store)),
        "real_shops": db.scalar(select(func.count()).select_from(Store).where(Store.is_sample.is_(False))),
    }
