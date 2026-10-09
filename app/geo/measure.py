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

    area_m2 = length_m = None
    if polys:
        area_m2 = project_local(MultiPolygon(polys), "laea").area
    if lines:
        length_m = project_local(MultiLineString(lines), "aeqd").length

    reason = None
    if area_m2 is None and length_m is None:
        if unsupported:
            reason = f"unsupported geometry type: {unsupported[0].geom_type}"
        else:
            reason = "not applicable"
    return Measurement(area_m2, length_m, is_valid, validity_reason, reason)
