from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class FileRecord(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))  # metadata only
    format: Mapped[str | None] = mapped_column(String(16))  # "kml" | "shapefile"
    crs: Mapped[str | None] = mapped_column(String(255))  # source CRS label
    status: Mapped[str] = mapped_column(String(16))  # "COMPLETED" | "FAILED"
    feature_count: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class FeatureRecord(Base):
    __tablename__ = "features"

    file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("files.id", ondelete="CASCADE"), primary_key=True
    )
    # position in the source file; gaps are possible (skipped deleted shapefile records)
    index: Mapped[int] = mapped_column("feature_index", Integer, primary_key=True)
    source_id: Mapped[str | None] = mapped_column(String(255))
    geometry_type: Mapped[str | None] = mapped_column(String(32))
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # GeoJSON, EPSG:4326
    properties: Mapped[dict[str, Any]] = mapped_column(JSON)
    measurement: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_valid: Mapped[bool] = mapped_column(Boolean)
    validity_reason: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
