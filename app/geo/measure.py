"""Area and length of WGS84 geometries, computed in local projected CRSs (metres)."""

from collections.abc import Iterator
from dataclasses import dataclass

import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from app.geo.crs import project_local


@dataclass(frozen=True)
class Measurement:
    area_m2: float | None
    length_m: float | None
    is_valid: bool
    validity_reason: str | None
    reason: str | None  # why nothing was measured (None when something was)


def _parts(geom: BaseGeometry) -> Iterator[BaseGeometry]:
    """Flatten Multi* and GeometryCollection into simple geometries."""
    if hasattr(geom, "geoms"):
        for part in geom.geoms:
            yield from _parts(part)
    else:
        yield geom


def measure(geom: BaseGeometry) -> Measurement:
    """Measure a geometry given in EPSG:4326 (lon, lat).

    Polygons give area, lines give length, points give nothing. Parts of
    multi-geometries are summed per measurement type.
    """
    geom = shapely.force_2d(geom)
    is_valid, validity_reason = True, None

    if geom.is_empty:
        return Measurement(None, None, is_valid, validity_reason, "empty geometry")

    parts = list(_parts(geom))
    polys = [p for p in parts if isinstance(p, Polygon)]
    lines = [p for p in parts if isinstance(p, LineString) and p.geom_type == "LineString"]
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
