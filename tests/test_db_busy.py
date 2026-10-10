"""A busy database (locked by another writer, or no free connection) is a retryable 503,
not a 500 (M9)."""

import sqlite3

import pytest
from sqlalchemy import event

from tests.api_helpers import SURVEY_KML, upload


@pytest.fixture
def locked_db(engine, client):
    """Another connection holds the write lock; ours give up after 100 ms instead of 30 s."""

    @event.listens_for(engine, "connect")
    def short_timeout(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA busy_timeout=100")

    engine.dispose()
    blocker = sqlite3.connect(engine.url.database, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    yield
    blocker.execute("ROLLBACK")
    blocker.close()


def test_upload_while_database_is_locked_is_503_with_retry_after(client, locked_db):
    resp = upload(client, SURVEY_KML)
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "2"
    err = resp.json()["error"]
    assert err["code"] == "database_busy"
    assert set(resp.json()) == {"error"}


@pytest.fixture
def exhausted_pool_client(tmp_path):
    """A one-connection pool whose only connection is taken; a request waits 0.1 s, then fails."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db.session import get_session, init_db
    from app.main import create_app

    engine = create_engine(
        f"sqlite:///{tmp_path / 'pool.db'}", pool_size=1, max_overflow=0, pool_timeout=0.1
    )
    init_db(engine)
    app = create_app()

    def override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override
    held = engine.connect()
    yield TestClient(app, raise_server_exceptions=False)
    held.close()
    engine.dispose()


def test_upload_when_no_database_connection_is_free_is_503(exhausted_pool_client):
    resp = upload(exhausted_pool_client, SURVEY_KML)
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "2"
    assert resp.json()["error"]["code"] == "database_busy"
