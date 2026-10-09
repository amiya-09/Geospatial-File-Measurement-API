"""Area and length of WGS84 geometries, computed in local projected CRSs (metres)."""

from collections.abc import Iterator
from dataclasses import dataclass

import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from app.geo.crs import project_local

# Slack for float noise from reprojection (~0.1 m); anything beyond is not lon/lat.
_RANGE_TOLERANCE_DEG = 1e-6


class MeasurementError(ValueError):
    """The geometry cannot be measured at all (e.g. coordinates are not lon/lat)."""


@dataclass(frozen=True)
class Measurement:
    area_m2: float | None
    length_m: float | None
    is_valid: bool
    validity_reason: str | None  # shapely's explanation when is_valid is False
    reason: str | None  # why nothing was measured (None when something was)
    # Unit and local CRS (PROJ string) behind each value; None when that value is None.
    area_unit: str | None = None
    area_crs: str | None = None
    length_unit: str | None = None
    length_crs: str | None = None


def _parts(geom: BaseGeometry) -> Iterator[BaseGeometry]:
    """Flatten Multi* and GeometryCollection into simple geometries."""
    if hasattr(geom, "geoms"):
        for part in geom.geoms:
            yield from _parts(part)
    else:
        yield geom


def _check_lon_lat(geom: BaseGeometry) -> None:
    minx, miny, maxx, maxy = geom.bounds
    tol = _RANGE_TOLERANCE_DEG
    if minx < -180 - tol or maxx > 180 + tol or miny < -90 - tol or maxy > 90 + tol:
        raise MeasurementError(
            f"coordinates are not valid lon/lat degrees (bounds: {minx}, {miny}, {maxx}, {maxy})"
        )


def measure(geom: BaseGeometry) -> Measurement:
    """Measure a geometry given in EPSG:4326 (lon, lat).

    Polygons give area, lines give length, points give nothing. Parts of
    multi-geometries are summed per measurement type. Invalid polygonal
    geometries are repaired with make_valid and only the polygonal pieces are
    measured; validity is reported either way.
    """
    geom = shapely.force_2d(geom)

    if geom.is_empty:
        return Measurement(None, None, True, None, "empty geometry")
    _check_lon_lat(geom)

    is_valid = geom.is_valid
    validity_reason = None if is_valid else shapely.is_valid_reason(geom)

    parts = list(_parts(geom))
    if not is_valid and all(isinstance(p, Polygon) for p in parts):
        parts = [p for p in _parts(shapely.make_valid(geom)) if isinstance(p, Polygon)]
        if not parts:
            return Measurement(None, None, False, validity_reason, "no polygonal area after repair")

    polys = [p for p in parts if isinstance(p, Polygon)]
    lines = [p for p in parts if p.geom_type == "LineString"]
    unsupported = [p for p in parts if not isinstance(p, Point | Polygon | LineString)]
    unsupported += [p for p in parts if p.geom_type == "LinearRing"]

    area_m2 = length_m = area_crs = length_crs = None
    if polys:
        projected, crs = project_local(MultiPolygon(polys), "laea")
        area_m2, area_crs = projected.area, crs.srs
    if lines:
        projected, crs = project_local(MultiLineString(lines), "aeqd")
        length_m, length_crs = projected.length, crs.srs

    reason = None
    if area_m2 is None and length_m is None:
        reason = "unsupported" if unsupported else "not applicable"
    return Measurement(
        area_m2=area_m2,
        length_m=length_m,
        is_valid=is_valid,
        validity_reason=validity_reason,
        reason=reason,
        area_unit="m2" if area_m2 is not None else None,
        area_crs=area_crs,
        length_unit="m" if length_m is not None else None,
        length_crs=length_crs,
    )
