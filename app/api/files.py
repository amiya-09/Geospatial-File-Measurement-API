import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app import config
from app.db import repository
from app.db.models import FileRecord
from app.db.session import get_session
from app.errors import ApiError, ErrorResponse
from app.schemas import FeatureOut, FileInfo, FileListItem, MeasurementOut, MeasurementsResponse
from app.services import files as service

router = APIRouter(prefix="/api/files", tags=["files"])

SessionDep = Annotated[Session, Depends(get_session)]
# SQLite integers are signed 64 bit; a larger OFFSET cannot even be sent to the database.
MAX_OFFSET = 2**63 - 1


def _error_example(code: str, message: str, **extra: str) -> dict:
    return {
        "content": {
            "application/json": {"example": {"error": {"code": code, "message": message, **extra}}}
        }
    }


def _errors(*items: tuple[int, str, str, str]) -> dict:
    return {
        status: {"model": ErrorResponse, "description": desc, **_error_example(code, msg)}
        for status, desc, code, msg in items
    }


def _load(session: Session, file_id: uuid.UUID) -> FileRecord:
    record = repository.get_file(session, str(file_id))
    if record is None:
        raise ApiError(404, "not_found", "file not found")
    return record


@router.post(
    "/",
    status_code=201,
    response_model=FileInfo,
    summary="Upload a zipped Shapefile or a KML file",
    description=(
        "Parses the file, measures every feature (area for polygons, length for lines, in "
        "metres, computed in a local projected CRS) and stores the result. The format is "
        "chosen from the file content; the filename is only stored as metadata."
    ),
    responses=_errors(
        (
            400,
            "Unreadable, corrupt or unsafe file",
            "invalid_file",
            "file is not a valid zip archive",
        ),
        (
            413,
            "Upload larger than the size limit",
            "payload_too_large",
            "upload is larger than 26214400 bytes",
        ),
        (
            415,
            "Neither a zipped Shapefile nor KML",
            "unsupported_media_type",
            "unsupported file type: expected a zipped Shapefile or KML",
        ),
        (
            422,
            "Readable but not measurable; a FAILED record was stored (file_id)",
            "unmeasurable_file",
            "no .prj file and coordinates are not longitude/latitude",
        ),
        (
            503,
            "Database busy under heavy load; retry after the Retry-After seconds",
            "database_busy",
            "The database is busy; retry shortly",
        ),
    ),
)
def upload_file(session: SessionDep, file: Annotated[UploadFile, File(description="zip or kml")]):
    data = service.read_limited(file.file, config.MAX_UPLOAD_BYTES)
    return service.create_file(session, file.filename, data)


@router.get(
    "/",
    response_model=list[FileListItem],
    summary="List uploaded files, newest first",
)
def list_files(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
):
    return repository.list_files(session, limit, offset)


@router.get(
    "/{file_id}/",
    response_model=FileInfo,
    summary="Get an uploaded file's details",
    responses=_errors((404, "No such file", "not_found", "file not found")),
)
def get_file(file_id: uuid.UUID, session: SessionDep):
    return _load(session, file_id)


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    summary="Get per-feature measurements",
    description=(
        "Features come back in file order with a stable `index`. Geometry is GeoJSON in "
        "EPSG:4326 whatever the source CRS; `crs` is the source CRS. Each measured value "
        "carries its unit and the local CRS used. Pass `include_geometry=false` to omit geometry; "
        "`limit` (1-1000) and `offset` page through the features and `total` is the full count."
    ),
    responses=_errors(
        (404, "No such file", "not_found", "file not found"),
        (
            409,
            "The file failed processing; the stored error is returned",
            "file_failed",
            "no .prj file and coordinates are not longitude/latitude",
        ),
    ),
)
def get_measurements(
    file_id: uuid.UUID,
    session: SessionDep,
    include_geometry: Annotated[bool, Query(description="Include GeoJSON geometry")] = True,
    limit: Annotated[int | None, Query(ge=1, le=1000, description="Page size")] = None,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET, description="Features to skip")] = 0,
):
    record = _load(session, file_id)
    if record.status == service.FAILED:
        raise ApiError(409, "file_failed", record.error or "file failed processing")
    features = [
        FeatureOut(
            index=f.index,
            source_id=f.source_id,
            geometry_type=f.geometry_type,
            crs=record.crs,
            properties=f.properties,
            measurement=MeasurementOut(**f.measurement),
            is_valid=f.is_valid,
            validity_reason=f.validity_reason,
            warnings=f.warnings,
            geometry=f.geometry if include_geometry else None,
        )
        for f in repository.get_features(session, record.id, limit, offset)
    ]
    return MeasurementsResponse(
        file_id=record.id,
        crs=record.crs,
        total=repository.count_features(session, record.id),
        limit=limit,
        offset=offset,
        features=features,
    )
