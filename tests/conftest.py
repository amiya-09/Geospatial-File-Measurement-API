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
