from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FeatureRecord, FileRecord


def add_file_with_features(
    session: Session, file: FileRecord, features: Sequence[FeatureRecord]
) -> None:
    """Write a file and all its features in ONE transaction (all or nothing)."""
    try:
        session.add(file)
        session.flush()  # the file row must exist before features reference it (FK)
        session.add_all(features)
        session.commit()
    except BaseException:
        session.rollback()
        raise


def get_file(session: Session, file_id: str) -> FileRecord | None:
    return session.get(FileRecord, file_id)


def get_features(session: Session, file_id: str) -> list[FeatureRecord]:
    query = (
        select(FeatureRecord).where(FeatureRecord.file_id == file_id).order_by(FeatureRecord.index)
    )
    return list(session.scalars(query))


def list_files(session: Session, limit: int, offset: int) -> list[FileRecord]:
    query = (
        select(FileRecord)
        .order_by(FileRecord.created_at.desc(), FileRecord.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(query))
