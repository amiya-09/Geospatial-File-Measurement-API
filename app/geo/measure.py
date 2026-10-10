"""Area and length of WGS84 geometries, computed in local projected CRSs (metres)."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from app import config
from app.geo.crs import project_local


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
    part_count: int = 0  # simple parts measured (each in its own projection), after any repair


def _parts(geom: BaseGeometry) -> Iterator[BaseGeometry]:
    """Flatten Multi* and GeometryCollection into simple, non-empty geometries.

    Empty parts are skipped: their bounds are NaN, which passes every span comparison.
    """
    if hasattr(geom, "geoms"):
        for part in geom.geoms:
            yield from _parts(part)
    elif not geom.is_empty:
        yield geom


def _lon_span(geom: BaseGeometry) -> float:
    minx, _, maxx, _ = geom.bounds
    return maxx - minx


def _degree_measure(geom: BaseGeometry) -> float:
    """Planar size in degrees (area for polygons, length for lines); only compared to zero."""
    return geom.area if isinstance(geom, Polygon) else geom.length


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
            f"feature spans more than 180 degrees of longitude ({min(span, alt_span):.1f}) "
            "even after unwrapping the antimeridian; it cannot be unwrapped or measured"
        )
    # Now span > 180 >= alt_span (parts are non-empty with finite coordinates, so both spans
    # are real numbers): the shifted part is the narrower one. -180 and 180 are the same
    # meridian, so a feature that goes all the way round collapses to nothing when shifted.
    # Never trade a real feature for an empty one.
    if _degree_measure(alternative) == 0 and _degree_measure(part) > 0:
        raise MeasurementError(
            "feature spans more than 180 degrees of longitude and collapses to zero "
            "size when unwrapped across the antimeridian; it cannot be unwrapped or measured"
        )
    return alternative, True


def _measure_parts(parts: Sequence[BaseGeometry], kind: str) -> tuple[float, str]:
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


def _repair(polys: list[Polygon]) -> list[Polygon]:
    """Repair a feature's polygons together, as one multipolygon.

    The "structure" method unions overlapping polygons and subtracts holes, so a polygon
    written twice counts once. Returns the polygonal pieces.
    """
    shape = polys[0] if len(polys) == 1 else MultiPolygon(polys)
    repaired = shapely.make_valid(shape, method="structure", keep_collapsed=False)
    return [p for p in _parts(repaired) if isinstance(p, Polygon) and not p.is_empty]


def _check_lon_lat(geom: BaseGeometry) -> None:
    minx, miny, maxx, maxy = geom.bounds
    # Bounds ignore NaN vertices and NaN compares False against every bound, so check the
    # coordinates themselves.
    if not np.isfinite(shapely.get_coordinates(geom)).all():
        raise MeasurementError("coordinates are not finite numbers")
    # No tolerance: a latitude even slightly past a pole projects to infinity.
    if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
        raise MeasurementError(
            f"coordinates are not valid lon/lat degrees (bounds: {minx}, {miny}, {maxx}, {maxy})"
        )


def measure(geom: BaseGeometry) -> Measurement:
    """Measure a geometry given in EPSG:4326 (lon, lat).

    Polygons give area, lines give length, points give nothing. Each part is unwrapped
    across the antimeridian first. All polygons of the geometry (including those inside a
    mixed collection) are then checked together and, if invalid, repaired together with
    make_valid. Each resulting part is measured in its own projection and the parts are
    summed; validity is reported either way.
    """
    geom = shapely.force_2d(geom)

    if geom.is_empty:
        return Measurement(None, None, True, None, "empty geometry")
    _check_lon_lat(geom)

    # Unwrap every part first: a shape that crosses 180 only looks invalid in raw lon/lat,
    # so validity and repair must see the unwrapped coordinates.
    polys: list[Polygon] = []
    lines: list[BaseGeometry] = []
    others: list[BaseGeometry] = []
    unwrapped = 0
    for part in _parts(geom):
        if isinstance(part, Polygon) or part.geom_type == "LineString":
            part, shifted = _unwrap_antimeridian(part)
            unwrapped += shifted
        if isinstance(part, Polygon):
            polys.append(part)
        elif part.geom_type == "LineString":
            lines.append(part)
        else:
            others.append(part)

    # The polygons of one feature are judged together, whatever else the feature holds, so
    # overlapping members count as invalid and are repaired as one multipolygon.
    polygonal = (polys[0] if len(polys) == 1 else MultiPolygon(polys)) if polys else None
    checked = ([polygonal] if polygonal is not None else []) + lines
    invalid = next((g for g in checked if not g.is_valid), None)
    is_valid = invalid is None
    validity_reason = None if invalid is None else shapely.is_valid_reason(invalid)

    warnings: list[str] = []
    repaired_to_nothing = False
    written_m2: float | None = None  # area of the polygons as written, when they were repaired
    if polygonal is not None and not polygonal.is_valid:
        vertices = shapely.get_num_coordinates(polygonal)
        if vertices > config.MAX_REPAIR_VERTICES:
            raise MeasurementError(
                f"polygon is invalid ({validity_reason}) and has {vertices} vertices; "
                f"repair is limited to {config.MAX_REPAIR_VERTICES} vertices"
            )
        written_m2 = _measure_parts(polys, "laea")[0]
        polys = _repair(polys)
        repaired_to_nothing = not polys

    unsupported = [p for p in others if not isinstance(p, Point)]
    unsupported += [p for p in lines if p.geom_type == "LinearRing"]

    area_m2 = length_m = area_crs = length_crs = None
    if polys:
        area_m2, area_crs = _measure_parts(polys, "laea")
    if written_m2 is not None:
        after = area_m2 or 0.0
        if abs(after - written_m2) > 1e-6 * max(abs(after), abs(written_m2)):
            warnings.append(
                f"repair changed the area: the polygons as written sum to {written_m2:,.0f} m2, "
                f"the repaired shape is {after:,.0f} m2"
            )
    if lines:
        length_m, length_crs = _measure_parts(lines, "aeqd")
    if unwrapped:
        warnings.append(
            f"antimeridian: negative longitudes were shifted by +360 for {unwrapped} part(s) "
            "that spanned more than 180 degrees of longitude"
        )

    reason = None
    if area_m2 is None and length_m is None:
        if repaired_to_nothing:
            reason = "no polygonal area after repair"
        else:
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
        warnings=tuple(warnings),
        part_count=len(polys) + len(lines),
    )
