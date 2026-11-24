from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_roles, resolve_region_scope
from ..models import SEVERITIES, Category, Outlet, Product, Region, Role, SlaRule, Store, StoreStock, User, utcnow
from ..security import hash_password
from ..services import ops

router = APIRouter(prefix="/api", tags=["admin"])
AdminUser = Depends(require_roles(Role.admin))


@router.get("/users/assignable")
def assignable_users(
    region_id: int | None = None, user: User = Depends(require_roles(Role.admin, Role.region_admin)),
    db: Session = Depends(get_db),
):
    scope = resolve_region_scope(user, region_id)
    stmt = select(User).where(User.active.is_(True))
    if scope is not None:
        stmt = stmt.where(
            or_(and_(User.region_id == scope, User.role.in_([Role.officer.value, Role.region_admin.value])),
                User.role == Role.admin.value)
        )
    else:
        stmt = stmt.where(User.role.in_([Role.officer.value, Role.region_admin.value, Role.admin.value]))
    return [{"id": u.id, "name": u.name, "role": u.role, "region_id": u.region_id} for u in db.scalars(stmt.order_by(User.name))]


class RegionBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    places: list[str] = Field(default_factory=list, max_length=200)  # towns, districts, landmarks in this region


@router.post("/admin/regions", status_code=201)
def create_region(body: RegionBody, user: User = AdminUser, db: Session = Depends(get_db)):
    r = Region(name=body.name.strip(), places=[p.strip() for p in body.places if p.strip()])
    db.add(r)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Region already exists")
    ops.audit(db, user, "admin.region.create", r.name)
    db.commit()
    return {"id": r.id, "name": r.name}


class OutletBody(BaseModel):
    outlet_code: str = Field(min_length=1, max_length=30)
    outlet_name: str = Field(min_length=1, max_length=150)
    area: str = Field(default="", max_length=100)
    region_id: int


@router.post("/admin/outlets", status_code=201)
def create_outlet(body: OutletBody, user: User = AdminUser, db: Session = Depends(get_db)):
    if db.get(Region, body.region_id) is None:
        raise HTTPException(status_code=422, detail="Unknown region")
    o = Outlet(outlet_code=body.outlet_code.strip(), outlet_name=body.outlet_name.strip(), area=body.area.strip(), region_id=body.region_id)
    db.add(o)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Outlet code already exists")
    ops.audit(db, user, "admin.outlet.create", o.outlet_code)
    db.commit()
    return {"id": o.id}


class UserBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=10, max_length=72)
    role: Role
    region_id: int | None = None


@router.post("/admin/users", status_code=201)
def create_user(body: UserBody, user: User = AdminUser, db: Session = Depends(get_db)):
    needs_region = body.role in (Role.region_admin, Role.officer)
    if needs_region and (body.region_id is None or db.get(Region, body.region_id) is None):
        raise HTTPException(status_code=422, detail="A valid region_id is required for this role")
    u = User(
        name=body.name.strip(),
        email=str(body.email).lower(),
        password_hash=hash_password(body.password),
        role=body.role.value,
        region_id=body.region_id if needs_region else None,
    )
    db.add(u)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Email already registered")
    ops.audit(db, user, "admin.user.create", f"{u.email} role={u.role}")
    db.commit()
    return {"id": u.id}


class CategoryBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    active: bool = True


@router.get("/admin/categories")
def list_categories(user: User = AdminUser, db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name, "description": c.description, "active": c.active}
            for c in db.scalars(select(Category).order_by(Category.id))]


@router.put("/admin/categories")
def upsert_category(body: CategoryBody, user: User = AdminUser, db: Session = Depends(get_db)):
    c = db.scalar(select(Category).where(Category.name == body.name.strip()))
    if c is None:
        c = Category(name=body.name.strip())
        db.add(c)
    if c.name == "Other" and not body.active:
        raise HTTPException(status_code=422, detail="The 'Other' category must stay active")
    c.description, c.active = body.description, body.active
    ops.audit(db, user, "admin.category.upsert", f"{c.name} active={c.active}")
    db.commit()
    return {"name": c.name, "active": c.active}


class SlaBody(BaseModel):
    severity: str
    target_hours: int = Field(ge=1, le=24 * 365)


@router.put("/admin/sla")
def set_sla(body: SlaBody, user: User = AdminUser, db: Session = Depends(get_db)):
    if body.severity not in SEVERITIES:
        raise HTTPException(status_code=422, detail="Unknown severity")
    db.merge(SlaRule(severity=body.severity, target_hours=body.target_hours))
    ops.audit(db, user, "admin.sla.set", f"{body.severity}={body.target_hours}h")
    db.commit()
    return {"severity": body.severity, "target_hours": body.target_hours}


@router.post("/admin/escalations/run")
def run_escalations(user: User = AdminUser, db: Session = Depends(get_db)):
    return {"escalated": ops.run_escalations(db)}


class ProductBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=40)
    size_ml: int = Field(ge=1, le=10000)
    price: float = Field(gt=0, le=1_000_000)


@router.post("/admin/products", status_code=201)
def create_product(body: ProductBody, user: User = AdminUser, db: Session = Depends(get_db)):
    p = Product(name=body.name.strip(), category=body.category.strip(), size_ml=body.size_ml, price=body.price)
    db.add(p)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="That product and size already exist")
    ops.audit(db, user, "admin.product.create", f"{p.name} {p.size_ml}ml")
    db.commit()
    return {"id": p.id}


class StoreBody(BaseModel):
    shop_code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(default="", max_length=400)
    district: str = Field(min_length=1, max_length=100)
    area: str = Field(default="", max_length=100)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


@router.post("/admin/stores", status_code=201)
def create_store(body: StoreBody, user: User = AdminUser, db: Session = Depends(get_db)):
    """Add one shop to the find-stores directory. Use `python -m app.import_stores` for a whole list."""
    s = Store(shop_code=body.shop_code.strip(), name=body.name.strip(), address=body.address.strip(), district=body.district.strip(),
              area=body.area.strip(), lat=body.lat, lng=body.lng, source="manual")
    db.add(s)
    try:
        db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="A shop with that code already exists")
    ops.audit(db, user, "admin.store.create", s.shop_code)
    db.commit()
    return {"id": s.id}


class StockBody(BaseModel):
    store_id: int
    product_id: int
    quantity: int = Field(ge=0, le=100_000)


@router.put("/stock")
def set_stock(body: StockBody, user: User = AdminUser, db: Session = Depends(get_db)):
    """Set the quantity of one product at one shop. This is the hook for a real inventory feed."""
    store = db.get(Store, body.store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    if db.get(Product, body.product_id) is None:
        raise HTTPException(status_code=404, detail="Product not found")
    db.merge(StoreStock(store_id=store.id, product_id=body.product_id, quantity=body.quantity, updated_at=utcnow()))
    ops.audit(db, user, "stock.set", f"store={store.id} product={body.product_id} qty={body.quantity}")
    db.commit()
    return {"store_id": store.id, "product_id": body.product_id, "quantity": body.quantity}