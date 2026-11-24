import enum
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .config import settings
from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Stores UTC, always returns timezone-aware UTC datetimes (also on SQLite)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


EmbeddingType = Vector(settings.embedding_dim).with_variant(JSON(), "sqlite")


class Role(str, enum.Enum):
    admin = "admin"  # all regions, full control
    management = "management"  # all regions, read-only dashboards
    region_admin = "region_admin"  # one region: review, assign, resolve
    officer = "officer"  # one region: works assigned grievances


class Status(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    PENDING_REVIEW = "PENDING_REVIEW"
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"
    DUPLICATE = "DUPLICATE"


OPEN_STATUSES = [
    Status.SUBMITTED.value,
    Status.PENDING_REVIEW.value,
    Status.OPEN.value,
    Status.ASSIGNED.value,
    Status.IN_PROGRESS.value,
    Status.REOPENED.value,
]

SEVERITIES = ["Low", "Medium", "High", "Critical"]
SENTIMENTS = ["Negative", "Neutral", "Positive"]
REL_TYPES = ["duplicate", "related"]


class Region(Base):
    __tablename__ = "regions"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    # Towns, districts and landmarks that belong to this region. Used to match what a citizen types.
    places: Mapped[list] = mapped_column(JSON, default=list)
    outlets: Mapped[list["Outlet"]] = relationship(back_populates="region")


class Outlet(Base):
    __tablename__ = "outlets"
    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_code: Mapped[str] = mapped_column(String(30), unique=True)
    outlet_name: Mapped[str] = mapped_column(String(150))
    area: Mapped[str] = mapped_column(String(100), default="")  # locality, e.g. "Anna Nagar"
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)  # WGS84, used by the nearby-store search
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    region: Mapped[Region] = relationship(back_populates="outlets")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))
    region_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class RecurringIssue(Base):
    __tablename__ = "recurring_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    category: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Grievance(Base):
    __tablename__ = "grievances"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str | None] = mapped_column(String(30), unique=True, nullable=True)
    tracking_token_hash: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)  # immutable after insert
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    outlet_id: Mapped[int | None] = mapped_column(ForeignKey("outlets.id"), nullable=True, index=True)
    contact_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=Status.SUBMITTED.value, index=True)
    review_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    recurring_issue_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurring_issues.id"), nullable=True, index=True
    )
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    region: Mapped[Region] = relationship()
    outlet: Mapped[Outlet | None] = relationship()
    assigned_user: Mapped[User | None] = relationship()
    recurring_issue: Mapped[RecurringIssue | None] = relationship()


@event.listens_for(Grievance, "before_update")
def _description_is_immutable(mapper, connection, target):
    if inspect(target).attrs.description.history.has_changes():
        raise ValueError("Grievance description is immutable")


class GrievanceAnalysis(Base):
    """One row per AI (or human) analysis run. The highest id is the current analysis."""

    __tablename__ = "grievance_analysis"
    id: Mapped[int] = mapped_column(primary_key=True)
    grievance_id: Mapped[int] = mapped_column(ForeignKey("grievances.id"), index=True)
    category: Mapped[str] = mapped_column(String(80))
    related_categories: Mapped[list] = mapped_column(JSON, default=list)
    sentiment: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(20), index=True)
    summary: Mapped[str] = mapped_column(String(500))
    issues: Mapped[list] = mapped_column(JSON, default=list)  # alleged, unverified
    entities: Mapped[dict] = mapped_column(JSON, default=dict)
    requires_investigation: Mapped[bool] = mapped_column(Boolean, default=True)
    recommended_action: Mapped[str] = mapped_column(String(500), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(10), default="ai")  # ai | human
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class GrievanceEmbedding(Base):
    __tablename__ = "grievance_embeddings"
    grievance_id: Mapped[int] = mapped_column(ForeignKey("grievances.id"), primary_key=True)
    embedding = mapped_column(EmbeddingType)
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class GrievanceRelationship(Base):
    __tablename__ = "grievance_relationships"
    __table_args__ = (UniqueConstraint("grievance_id", "related_grievance_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    grievance_id: Mapped[int] = mapped_column(ForeignKey("grievances.id"), index=True)
    related_grievance_id: Mapped[int] = mapped_column(ForeignKey("grievances.id"), index=True)
    relationship_type: Mapped[str] = mapped_column(String(20))  # duplicate | related
    score: Mapped[float] = mapped_column(Float)
    rerank_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    grievance_id: Mapped[int] = mapped_column(ForeignKey("grievances.id"), index=True)
    assigned_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assigned_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class GrievanceAction(Base):
    __tablename__ = "grievance_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    grievance_id: Mapped[int] = mapped_column(ForeignKey("grievances.id"), index=True)
    action: Mapped[str] = mapped_column(String(60))
    remarks: Mapped[str] = mapped_column(Text, default="")
    performed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # None = system/citizen
    public: Mapped[bool] = mapped_column(Boolean, default=False)  # visible on public tracking page
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    performed_by: Mapped[User | None] = relationship()


class SlaRule(Base):
    __tablename__ = "sla_rules"
    severity: Mapped[str] = mapped_column(String(20), primary_key=True)
    target_hours: Mapped[int] = mapped_column(Integer)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(String(500), default="")
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Product(Base):
    """Something an outlet can stock. Prices are the printed MRP in INR."""

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("name", "size_ml"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40), index=True)
    size_ml: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Store(Base):
    """A retail shop for the "find stock near me" feature. Separate from `outlets`, which the grievance flow uses,
    so loading thousands of shops here does not affect complaints. `outlet_id` can link a shop to a grievance outlet."""

    __tablename__ = "stores"
    id: Mapped[int] = mapped_column(primary_key=True)
    shop_code: Mapped[str] = mapped_column(String(40), unique=True)  # official shop number
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(400), default="")
    district: Mapped[str] = mapped_column(String(100), index=True)
    area: Mapped[str] = mapped_column(String(100), default="")
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(120), default="manual")  # "sample", "import:<file>", "manual"
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    outlet_id: Mapped[int | None] = mapped_column(ForeignKey("outlets.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class StoreStock(Base):
    __tablename__ = "store_stock"
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

class ChatSession(Base):
    """State of one citizen's intake conversation. The session id is the credential, so it is random and unguessable."""

    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=0)  # compare-and-swap guard against double submits
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


Index("ix_grievances_region_status", Grievance.region_id, Grievance.status)
