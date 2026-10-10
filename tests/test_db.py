import threading

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, StatementError

from app.db import repository
from app.db.models import FeatureRecord, FileRecord
from app.db.session import init_db


def file_record(file_id="11111111-1111-4111-8111-111111111111", **overrides):
    values = {
        "id": file_id,
        "filename": "survey.kml",
        "format": "kml",
        "crs": "EPSG:4326",
        "status": "COMPLETED",
        "feature_count": 2,
        "error": None,
        "warnings": ["w1"],
    }
    values.update(overrides)
    return FileRecord(**values)


def feature_record(file_id, index, **overrides):
    values = {
        "file_id": file_id,
        "index": index,
        "source_id": f"s{index}",
        "geometry_type": "Point",
        "geometry": {"type": "Point", "coordinates": [1.5, 2.5]},
        "properties": {"name": f"f{index}", "n": 3},
        "measurement": {"area_m2": None, "length_m": None, "reason": "not applicable"},
        "is_valid": True,
        "validity_reason": None,
        "warnings": [],
    }
    values.update(overrides)
    return FeatureRecord(**values)


def test_file_and_features_round_trip_with_json_columns(session_factory):
    fid = "11111111-1111-4111-8111-111111111111"
    with session_factory() as session:
        repository.add_file_with_features(
            session, file_record(fid), [feature_record(fid, 1), feature_record(fid, 0)]
        )
    with session_factory() as session:
        stored = repository.get_file(session, fid)
        assert (stored.filename, stored.crs, stored.status, stored.warnings) == (
            "survey.kml",
            "EPSG:4326",
            "COMPLETED",
            ["w1"],
        )
        assert stored.created_at is not None
        features = repository.get_features(session, fid)
        assert [f.index for f in features] == [0, 1]  # ordered by index
        assert features[0].geometry == {"type": "Point", "coordinates": [1.5, 2.5]}
        assert features[0].properties == {"name": "f0", "n": 3}
        assert features[0].measurement["reason"] == "not applicable"


def test_unknown_file_is_none(session_factory):
    with session_factory() as session:
        assert repository.get_file(session, "00000000-0000-4000-8000-000000000000") is None


def test_file_and_features_are_written_in_one_transaction(session_factory):
    fid = "11111111-1111-4111-8111-111111111111"
    duplicate_index = [feature_record(fid, 0), feature_record(fid, 0)]  # primary key clash
    with session_factory() as session:
        with pytest.raises(IntegrityError):
            repository.add_file_with_features(session, file_record(fid), duplicate_index)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FileRecord)) == 0
        assert session.scalar(select(func.count()).select_from(FeatureRecord)) == 0


def test_foreign_keys_are_enforced(session_factory):
    with session_factory() as session:
        session.add(feature_record("22222222-2222-4222-8222-222222222222", 0))
        with pytest.raises(IntegrityError):
            session.commit()


def test_non_finite_floats_cannot_be_stored_as_json(session_factory):
    # Safety net: the service converts them to null first; the engine refuses NaN outright.
    fid = "11111111-1111-4111-8111-111111111111"
    bad = feature_record(fid, 0, properties={"x": float("nan")})
    with session_factory() as session:
        with pytest.raises(StatementError, match="Out of range float values"):
            repository.add_file_with_features(session, file_record(fid), [bad])
    with session_factory() as session:
        assert repository.get_file(session, fid) is None


def test_connection_can_be_used_from_another_thread(session_factory):
    errors = []

    def worker():
        try:
            with session_factory() as session:
                session.execute(text("select 1"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with session_factory() as main_session:
        main_session.execute(text("select 1"))
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
    assert errors == []


def test_init_db_is_idempotent(engine):
    init_db(engine)
    init_db(engine)


def test_list_files_newest_first_with_paging(session_factory):
    ids = [f"00000000-0000-4000-8000-00000000000{i}" for i in range(3)]
    with session_factory() as session:
        for fid in ids:
            repository.add_file_with_features(session, file_record(fid, feature_count=0), [])
    with session_factory() as session:
        assert [f.id for f in repository.list_files(session, limit=2, offset=0)] == ids[::-1][:2]
        assert [f.id for f in repository.list_files(session, limit=2, offset=2)] == ids[::-1][2:]


def test_connections_use_wal_and_a_5_second_busy_timeout(engine):
    # Long enough for normal write queues, short enough that a client hears "retry" quickly.
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5_000
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_waiting_for_a_pooled_connection_gives_up_after_5_seconds_too(engine):
    assert engine.pool.timeout() == 5
