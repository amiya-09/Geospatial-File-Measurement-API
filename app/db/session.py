import json
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session

from app import config
from app.db.models import Base

BUSY_TIMEOUT_MS = 30_000


def _strict_json(value: object) -> str:
    # NaN/inf are not valid JSON; callers must convert them first, so fail loudly if they don't.
    return json.dumps(value, allow_nan=False)


def make_engine(path: Path | str) -> Engine:
    # check_same_thread=False: sync FastAPI dependencies and handlers run on pool threads.
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": BUSY_TIMEOUT_MS / 1000},
        json_serializer=_strict_json,
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        # WAL: readers do not block the writer and vice versa. busy_timeout: concurrent
        # writers queue for the lock instead of failing with "database is locked".
        dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.execute("PRAGMA synchronous=NORMAL")
        dbapi_connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")

    return engine


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


@lru_cache
def get_engine() -> Engine:
    return make_engine(config.DATABASE_PATH)


def get_session() -> Iterator[Session]:
    """One session per request. Tests override this dependency with a temp database."""
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session
