"""Upload handling: read safely, pick the parser by content, measure, persist."""

import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, BinaryIO

from pyproj.exceptions import ProjError
from sqlalchemy.orm import Session

from app import config
from app.db import repository
from app.db.models import FeatureRecord, FileRecord
from app.errors import RETRY_AFTER_SECONDS, ApiError
from app.geo.crs import crs_label, to_wgs84, wgs84_transformer
from app.geo.measure import MeasurementError, measure
from app.parsers.base import ParsedFile, ParseError, UnmeasurableError
from app.parsers.kml import NotKmlError, parse_kml
from app.parsers.shapefile import ZIP_SIGNATURE, parse_shapefile_zip
from app.services.serialize import json_safe, to_geojson

logger = logging.getLogger("app")

CHUNK_SIZE = 64 * 1024
MAX_FILENAME = 255
COMPLETED, FAILED = "COMPLETED", "FAILED"

processing_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_UPLOADS)


@contextmanager
def processing_slot() -> Iterator[None]:
    """Hold one of the few upload processing slots, or raise 503 after a short wait."""
    if not processing_slots.acquire(timeout=config.UPLOAD_SLOT_WAIT_SECONDS):
        raise ApiError(
            503,
            "server_busy",
            "The server is busy processing other uploads; retry shortly",
            headers={"Retry-After": RETRY_AFTER_SECONDS},
        )
    try:
        yield
    finally:
        processing_slots.release()


def read_limited(source: BinaryIO, limit: int, chunk_size: int = CHUNK_SIZE) -> bytes:
    """Read a stream in chunks, raising 413 as soon as more than ``limit`` bytes arrive.

    Counts bytes actually read, so it does not rely on the Content-Length header.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = source.read(min(chunk_size, limit + 1 - total))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise ApiError(413, "payload_too_large", f"upload is larger than {limit} bytes")
        chunks.append(chunk)


def clean_filename(name: str | None) -> str:
    """Metadata only: basename, printable characters, at most 255 long. Never used as a path."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    return base[:MAX_FILENAME] or "upload"


def _looks_like_xml(data: bytes) -> bool:
    return data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<")


def parse_upload(data: bytes) -> tuple[str, ParsedFile]:
    """Choose the parser by content (never by filename). Returns (format, parsed)."""
    if not data.strip():
        raise ApiError(400, "invalid_file", "the uploaded file is empty")
    if data.startswith(ZIP_SIGNATURE):
        try:
            return "shapefile", parse_shapefile_zip(data)
        except ParseError as exc:
            raise ApiError(400, "invalid_file", str(exc)) from exc
    try:
        return "kml", parse_kml(data)
    except NotKmlError as exc:
        raise ApiError(415, "unsupported_media_type", str(exc)) from exc
    except ParseError as exc:
        if _looks_like_xml(data):  # it claims to be XML but is broken: a bad KML
            raise ApiError(400, "invalid_file", str(exc)) from exc
        raise ApiError(
            415,
            "unsupported_media_type",
            "unsupported file type: expected a zipped Shapefile or KML",
        ) from exc


def _measure_features(
    parsed: ParsedFile, file_id: str
) -> tuple[list[FeatureRecord], list[str], str]:
    # Features whose geometry was dropped are kept, but a file with nothing left to measure is
    # unmeasurable rather than a success full of empty features.
    if parsed.features and all(f.geometry is None or f.geometry.is_empty for f in parsed.features):
        first = f" (first warning: {parsed.warnings[0]})" if parsed.warnings else ""
        raise MeasurementError(
            f"none of the {len(parsed.features)} features has a usable geometry: coordinates "
            f"were out of range or unusable{first}"
        )
    warnings = list(parsed.warnings)
    rows: list[FeatureRecord] = []
    label = crs_label(parsed.crs)
    parts_measured = 0
    transformer = None  # built at the first geometry, then reused for every feature
    for feature in parsed.features:
        geometry = geometry_type = None
        measurement: dict[str, Any]
        if feature.geometry is None:
            measurement = {"reason": "no geometry"}
            is_valid, validity_reason, feature_warnings = True, None, list(feature.warnings)
        else:
            try:
                if transformer is None:
                    transformer = wgs84_transformer(parsed.crs)
                wgs84 = to_wgs84(feature.geometry, parsed.crs, transformer)
                result = measure(wgs84)
                geometry = to_geojson(wgs84)
            except (MeasurementError, ProjError) as exc:
                raise MeasurementError(f"feature {feature.index}: {exc}") from exc
            # The parsers cap parts before building geometry; repair can add parts, so the
            # measured total is checked too.
            parts_measured += result.part_count
            if parts_measured > config.MAX_PARTS:
                raise MeasurementError(
                    f"file has more than {config.MAX_PARTS} parts after repair "
                    f"(reached at feature {feature.index})"
                )
            geometry_type = feature.geometry.geom_type
            measurement = {
                "area_m2": result.area_m2,
                "length_m": result.length_m,
                "area_unit": result.area_unit,
                "area_crs": result.area_crs,
                "length_unit": result.length_unit,
                "length_crs": result.length_crs,
                "reason": result.reason,
            }
            is_valid, validity_reason = result.is_valid, result.validity_reason
            feature_warnings = list(result.warnings)
            warnings += [f"feature {feature.index}: {w}" for w in feature_warnings]
        rows.append(
            FeatureRecord(
                file_id=file_id,
                index=feature.index,
                source_id=feature.source_id,
                geometry_type=geometry_type,
                geometry=geometry,
                properties=json_safe(feature.properties),
                measurement=measurement,
                is_valid=is_valid,
                validity_reason=validity_reason,
                warnings=feature_warnings,
            )
        )
    return rows, warnings, label


def create_file(session: Session, filename: str | None, source: BinaryIO) -> FileRecord:
    """Read, parse, measure and store an upload. Raises ApiError for every client-side failure.

    The upload is read into memory only once a processing slot is held, so an upload that
    waits for a slot, or is turned away, holds nothing but Starlette's spooled temp file.
    """
    with processing_slot():
        data = read_limited(source, config.MAX_UPLOAD_BYTES)
        try:
            fmt, parsed = parse_upload(data)
        except UnmeasurableError as exc:  # only the shapefile parser raises it (.prj problems)
            raise _fail(session, filename, "shapefile", None, exc) from exc
        file_id = str(uuid.uuid4())
        try:
            rows, warnings, label = _measure_features(parsed, file_id)
        except MeasurementError as exc:
            raise _fail(session, filename, fmt, crs_label(parsed.crs), exc, file_id) from exc
    record = FileRecord(
        id=file_id,
        filename=clean_filename(filename),
        format=fmt,
        crs=label,
        status=COMPLETED,
        feature_count=len(rows),
        error=None,
        warnings=warnings,
    )
    repository.add_file_with_features(session, record, rows)
    return record


def _fail(
    session: Session,
    filename: str | None,
    fmt: str,
    crs: str | None,
    exc: Exception,
    file_id: str | None = None,
) -> ApiError:
    """Store a FAILED record (no features) and build the 422 that points at it."""
    file_id = file_id or str(uuid.uuid4())
    record = FileRecord(
        id=file_id,
        filename=clean_filename(filename),
        format=fmt,
        crs=crs,
        status=FAILED,
        feature_count=0,
        error=str(exc),
        warnings=[],
    )
    repository.add_file_with_features(session, record, [])
    return ApiError(422, "unmeasurable_file", str(exc), file_id=file_id)
