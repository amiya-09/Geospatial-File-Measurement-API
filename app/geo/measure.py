"""Area and length of WGS84 geometries, computed in local projected CRSs (metres)."""

from collections.abc import Iterator
from dataclasses import dataclass

import shapely
from shapely.geometry import LineString, Point, Polygon
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


def _measure_parts(parts: list[BaseGeometry], kind: str) -> tuple[float, str]:
    """Sum the measure of each part, each in its own local projection.

    Returns (total, crs label). Parts are never projected together: a shared projection
    centred between distant parts would be accurate for none of them.
    """
    total, crs_strings = 0.0, []
    for part in parts:
        projected, crs = project_local(part, kind)
        total += projected.area if kind == "laea" else projected.length
        crs_strings.append(crs.srs)
    if len(parts) == 1:
        return total, crs_strings[0]
    return total, f"local +proj={kind} per part ({len(parts)} parts)"


def _check_lon_lat(geom: BaseGeometry) -> None:
    minx, miny, maxx, maxy = geom.bounds
    tol = _RANGE_TOLERANCE_DEG
    if minx < -180 - tol or maxx > 180 + tol or miny < -90 - tol or maxy > 90 + tol:
        raise MeasurementError(
            f"coordinates are not valid lon/lat degrees (bounds: {minx}, {miny}, {maxx}, {maxy})"
        )


def measure(geom: BaseGeometry) -> Measurement:
    """Measure a geometry given in EPSG:4326 (lon, lat).

    Polygons give area, lines give length, points give nothing. Each part of a
    multi-geometry is measured separately and the parts are summed. Invalid polygonal
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
        area_m2, area_crs = _measure_parts(polys, "laea")
    if lines:
        length_m, length_crs = _measure_parts(lines, "aeqd")

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
