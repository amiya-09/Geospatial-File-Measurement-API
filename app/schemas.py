from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class FileInfo(BaseModel):
    id: str
    filename: str
    format: str | None = Field(description='"kml" or "shapefile" (decided by content)')
    feature_count: int
    crs: str | None = Field(description="Source CRS, e.g. EPSG:32632")
    status: str = Field(description="COMPLETED or FAILED")
    warnings: list[str]
    error: str | None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "3f2b8c1e-5a47-4d0e-9a52-1c7f4e6b9d10",
                "filename": "parcels.zip",
                "format": "shapefile",
                "feature_count": 2,
                "crs": "EPSG:32632",
                "status": "COMPLETED",
                "warnings": ["feature 1: null shape skipped"],
                "error": None,
            }
        },
    )


class FileListItem(BaseModel):
    id: str
    filename: str
    status: str
    feature_count: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at")
    def _utc_z(self, value: datetime) -> str:
        # SQLite hands back naive datetimes; everything stored is UTC.
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class MeasurementOut(BaseModel):
    area_m2: float | None = None
    length_m: float | None = None
    area_unit: str | None = None
    area_crs: str | None = Field(
        None,
        description="Local projected CRS used: its PROJ string, or a short summary when each "
        "part of a multi-part geometry was projected on its own",
    )
    length_unit: str | None = None
    length_crs: str | None = None
    reason: str | None = Field(None, description="Why nothing was measured, if so")


class FeatureOut(BaseModel):
    index: int = Field(description="Position in the source file (stable)")
    source_id: str | None
    geometry_type: str | None
    crs: str | None = Field(description="Source CRS of the file")
    properties: dict[str, Any]
    measurement: MeasurementOut
    is_valid: bool
    validity_reason: str | None
    warnings: list[str]
    geometry: dict[str, Any] | None = Field(description="GeoJSON in EPSG:4326, or null")


class MeasurementsResponse(BaseModel):
    file_id: str
    crs: str | None
    total: int = Field(description="Features in the file, whatever limit/offset were used")
    limit: int | None = Field(description="The limit asked for; null means no limit")
    offset: int
    features: list[FeatureOut]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "file_id": "3f2b8c1e-5a47-4d0e-9a52-1c7f4e6b9d10",
                "crs": "EPSG:4326",
                "total": 1,
                "limit": None,
                "offset": 0,
                "features": [
                    {
                        "index": 0,
                        "source_id": None,
                        "geometry_type": "Polygon",
                        "crs": "EPSG:4326",
                        "properties": {"name": "parcel"},
                        "measurement": {
                            "area_m2": 12308309698.66,
                            "length_m": None,
                            "area_unit": "m2",
                            "area_crs": "+proj=laea +lat_0=0.5 +lon_0=0.5 +datum=WGS84 "
                            "+units=m +no_defs +type=crs",
                            "length_unit": None,
                            "length_crs": None,
                            "reason": None,
                        },
                        "is_valid": True,
                        "validity_reason": None,
                        "warnings": [],
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                        },
                    }
                ],
            }
        },
    )
