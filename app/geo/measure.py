"""Area and length of WGS84 geometries, computed in local projected CRSs (metres)."""

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
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
    warnings: tuple[str, ...] = ()


def _parts(geom: BaseGeometry) -> Iterator[BaseGeometry]:
    """Flatten Multi* and GeometryCollection into simple geometries."""
    if hasattr(geom, "geoms"):
        for part in geom.geoms:
            yield from _parts(part)
    else:
        yield geom


def _lon_span(geom: BaseGeometry) -> float:
    minx, _, maxx, _ = geom.bounds
    return maxx - minx


def _unwrap_antimeridian(part: BaseGeometry) -> tuple[BaseGeometry, bool]:
    """Fix a part written with wrapped longitudes (e.g. 179 -> -179 across the antimeridian).

    If the part spans more than 180 degrees of longitude, also try it with negative
    longitudes shifted by +360 and keep whichever span is smaller. Raises MeasurementError
    when neither is 180 degrees or less. Returns (part, whether the shift was applied).
    """
    span = _lon_span(part)
    if span <= 180:
        return part, False

    def shift(coords: np.ndarray) -> np.ndarray:
        shifted = coords.copy()
        shifted[:, 0] = np.where(shifted[:, 0] < 0, shifted[:, 0] + 360.0, shifted[:, 0])
        return shifted

    alternative = shapely.transform(part, shift)
    alt_span = _lon_span(alternative)
    if min(span, alt_span) > 180:
        raise MeasurementError(
            f"feature spans {min(span, alt_span):.1f} degrees of longitude even after "
            "unwrapping the antimeridian; it cannot be measured"
        )
    if alt_span < span:
        return alternative, True
    return part, False


def _measure_parts(parts: list[BaseGeometry], kind: str) -> tuple[float, str, int]:
    """Sum the measure of each part, each in its own local projection.

    Returns (total, crs label, number of parts unwrapped across the antimeridian). Parts are
    never projected together: a shared projection centred between distant parts would be
    accurate for none of them.
    """
    total, crs_strings, unwrapped = 0.0, [], 0
    for part in parts:
        part, shifted = _unwrap_antimeridian(part)
        unwrapped += shifted
        projected, crs = project_local(part, kind)
        total += projected.area if kind == "laea" else projected.length
        crs_strings.append(crs.srs)
    if len(parts) == 1:
        return total, crs_strings[0], unwrapped
    return total, f"local +proj={kind} per part ({len(parts)} parts)", unwrapped


def _check_lon_lat(geom: BaseGeometry) -> None:
    minx, miny, maxx, maxy = geom.bounds
    tol = _RANGE_TOLERANCE_DEG
    # Bounds ignore NaN vertices and NaN compares False against every bound, so check the
    # coordinates themselves.
    if not np.isfinite(shapely.get_coordinates(geom)).all():
        raise MeasurementError("coordinates are not finite numbers")
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
    unwrapped = 0
    if polys:
        area_m2, area_crs, n = _measure_parts(polys, "laea")
        unwrapped += n
    if lines:
        length_m, length_crs, n = _measure_parts(lines, "aeqd")
        unwrapped += n
    warnings = ()
    if unwrapped:
        warnings = (
            f"antimeridian: negative longitudes were shifted by +360 for {unwrapped} part(s) "
            "that spanned more than 180 degrees of longitude",
        )

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
        warnings=warnings,
    )
