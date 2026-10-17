from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from wellwell.config import get_settings
from wellwell.db import models  # noqa: F401  (registers every table on Base.metadata)
from wellwell.db.base import Base

VIEWS_DIR = Path(__file__).parent


def _enable_sqlite_pragmas(engine: Engine, read_only: bool) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        if read_only:
            cursor.execute("PRAGMA query_only=ON")
        else:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)
    if url.startswith("sqlite"):
        _enable_sqlite_pragmas(engine, read_only=False)
    return engine


@lru_cache
def get_read_only_engine() -> Engine:
    """Used by the agent's query tool, so even a query that slips past the SQL guard can't write."""
    url = get_settings().database_url
    if url.startswith("sqlite"):
        path = url.removeprefix("sqlite:///")
        engine = create_engine(
            f"sqlite:///file:{path}?mode=ro&uri=true", connect_args={"check_same_thread": False}
        )
        _enable_sqlite_pragmas(engine, read_only=True)
        return engine
    return create_engine(url, pool_pre_ping=True,
                         connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=5000"})


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_schema(drop_first: bool = False) -> None:
    engine = get_engine()
    if engine.url.drivername.startswith("sqlite"):
        Path(engine.url.database).parent.mkdir(parents=True, exist_ok=True)
        if drop_first:
            with engine.begin() as connection:
                for view in ("latest_inspection", "inspection_facts"):
                    connection.execute(text(f"DROP VIEW IF EXISTS {view}"))
            Base.metadata.drop_all(engine)
    elif drop_first:
        _reset_postgres(engine)
    Base.metadata.create_all(engine)


def _reset_postgres(engine: Engine, attempts: int = 6) -> None:
    """Drops everything in one statement. Readers may be mid-query, so wait briefly and retry instead of deadlocking."""
    import time

    from sqlalchemy.exc import OperationalError

    for attempt in range(attempts):
        try:
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
            return
        except OperationalError:
            if attempt == attempts - 1:
                raise
            time.sleep(1 + attempt)


def create_views() -> None:
    with get_engine().begin() as connection:
        views = "views_sqlite.sql" if connection.dialect.name == "sqlite" else "views_postgres.sql"
        for statement in (VIEWS_DIR / views).read_text().split(";\n"):
            if statement.strip():
                connection.execute(text(statement))
