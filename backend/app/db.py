from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=not _is_sqlite,
)
IS_POSTGRES = engine.dialect.name == "postgresql"

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    if IS_POSTGRES:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    _add_missing_columns()
    if IS_POSTGRES:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_grievance_embeddings_hnsw "
                    "ON grievance_embeddings USING hnsw (embedding vector_cosine_ops)"
                )
            )


# Columns added after the first release. create_all() never alters existing tables, so add them in place.
_LATER_COLUMNS = {
    "regions": {"places": "JSON"},
    "outlets": {"area": "VARCHAR(100) DEFAULT ''", "lat": "FLOAT", "lng": "FLOAT"},
}


def _add_missing_columns() -> None:
    from sqlalchemy import inspect

    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in _LATER_COLUMNS.items():
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))