import pytest
from sqlalchemy.orm import sessionmaker

from app.db.session import init_db, make_engine


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def client(session_factory):
    """TestClient on a fresh app whose DB dependency points at a temp database."""
    from fastapi.testclient import TestClient

    from app.db.session import get_session
    from app.main import create_app

    app = create_app()

    def override():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    # raise_server_exceptions=False: a 500 must come back as a response, as it would in production
    return TestClient(app, raise_server_exceptions=False)
