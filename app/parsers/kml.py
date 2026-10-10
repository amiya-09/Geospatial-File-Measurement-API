"""KML parser. Placemarks become features; coordinates are always WGS84 (lon, lat).

Problems with one Placemark's geometry (bad coordinates, missing or unsupported geometry)
keep the feature with ``geometry=None`` and add a warning, so indexes stay stable. Problems
with the file as a whole raise ParseError.
"""

import io
import math
import re
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as safe_et
from defusedxml import DefusedXmlException
from pyproj import CRS
from shapely.errors import ShapelyError
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.config import MAX_FEATURES, MAX_PARTS, MAX_VERTICES, MAX_XML_DEPTH, MAX_XML_ELEMENTS
from app.parsers.base import Budget, Feature, ParsedFile, ParseError


class NotKmlError(ParseError):
    """Well-formed XML, but the root element is not <kml>: some other kind of file."""


WGS84 = CRS.from_epsg(4326)

_GEOMETRY_TAGS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"}
_UNSUPPORTED_TAGS = {"Track", "MultiTrack", "Model"}
_PART_TAGS = {"LineString", "LinearRing"}
_COMMA_SPACES = re.compile(r"\s*,\s*")
_PIECE = re.compile(r"\S+")


class _GeometryError(ValueError):
    """One Placemark's geometry is unusable."""


def _local(tag: str) -> str:
    """Tag name without its XML namespace."""
    return tag.rsplit("}", 1)[-1]


def _children(el: Element, name: str) -> list[Element]:
    return [c for c in el if _local(c.tag) == name]


def _text(el: Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def _count_tokens(text: str, stop_after: int) -> int:
    """Number of coordinate tokens in ``text``, counting no further than ``stop_after + 1``.

    The tokens are those of ``_COMMA_SPACES.sub(",", text).split()``: whitespace next to a comma
    does not separate. They are found one at a time and nothing is kept for each.
    """
    count, joins_next = 0, False
    for match in _PIECE.finditer(text):
        start, end = match.span()
        if count == 0 or not (joins_next or text[start] == ","):
            count += 1
            if count > stop_after:
                break
        joins_next = text[end - 1] == ","
    return count


def _coordinates(el: Element, budget: Budget) -> list[tuple[float, float]]:
    """Parse a geometry's <coordinates>: whitespace-separated lon,lat[,alt]; altitude dropped."""
    nodes = _children(el, "coordinates")
    if not nodes:
        raise _GeometryError("missing <coordinates>")
    text = nodes[0].text or ""
    # Counted before splitting (splitting 6.5 million tokens first took 452 MB), and before any
    # float or geometry is built.
    budget.take(_count_tokens(text, stop_after=budget.limit - budget.used))
    tokens = _COMMA_SPACES.sub(",", text).split()
    points = []
    for token in tokens:
        parts = token.split(",")
        if len(parts) not in (2, 3):
            raise _GeometryError(f"bad coordinate {token!r}")
        try:
            values = [float(p) for p in parts]
        except ValueError:
            raise _GeometryError(f"bad coordinate {token!r}") from None
        if not all(math.isfinite(v) for v in values):
            raise _GeometryError(f"non-finite coordinate {token!r}")
        if not (-180 <= values[0] <= 180 and -90 <= values[1] <= 90):
            raise _GeometryError(f"coordinate {token!r} is out of the lon/lat range")
        points.append((values[0], values[1]))
    if not points:
        raise _GeometryError("empty <coordinates>")
    return points


def _ring(boundary: Element, budget: Budget) -> list[tuple[float, float]]:
    rings = _children(boundary, "LinearRing")
    if not rings:
        raise _GeometryError("boundary has no <LinearRing>")
    return _coordinates(rings[0], budget)


def _simple_geometries(el: Element, budget: Budget) -> list[BaseGeometry]:
    """Geometries for one geometry element; MultiGeometry is flattened recursively."""
    kind = _local(el.tag)
    try:
        if kind == "Point":
            points = _coordinates(el, budget)
            if len(points) != 1:
                raise _GeometryError("<Point> must have exactly one coordinate")
            return [Point(points[0])]
        if kind in ("LineString", "LinearRing"):
            return [LineString(_coordinates(el, budget))]
        if kind == "Polygon":
            outer = _children(el, "outerBoundaryIs")
            if not outer:
                raise _GeometryError("<Polygon> has no <outerBoundaryIs>")
            holes = [_ring(b, budget) for b in _children(el, "innerBoundaryIs")]
            return [Polygon(_ring(outer[0], budget), holes)]
    except (_GeometryError, ParseError):
        raise
    except (ValueError, ShapelyError) as exc:  # shapely: too few points, etc.
        raise _GeometryError(str(exc)) from exc
    if kind == "MultiGeometry":
        members = []
        for child in el:
            if _local(child.tag) in _GEOMETRY_TAGS | _UNSUPPORTED_TAGS:
                members.extend(_simple_geometries(child, budget))
        if not members:
            raise _GeometryError("empty <MultiGeometry>")
        return members
    raise _GeometryError(f"unsupported geometry <{kind}>")


def _combine(parts: list[BaseGeometry]) -> BaseGeometry:
    """Single part stays itself; same-type parts become Multi*; otherwise a collection."""
    if len(parts) == 1:
        return parts[0]
    types = {p.geom_type for p in parts}
    if types == {"Point"}:
        return MultiPoint(parts)
    if types == {"LineString"}:
        return MultiLineString(parts)
    if types == {"Polygon"}:
        return MultiPolygon(parts)
    return GeometryCollection(parts)


def _placemark_geometry(placemark: Element, budget: Budget) -> BaseGeometry:
    elements = [c for c in placemark if _local(c.tag) in _GEOMETRY_TAGS | _UNSUPPORTED_TAGS]
    if not elements:
        raise _GeometryError("no geometry")
    # A single bare geometry is returned as is. A <MultiGeometry> is flattened and recombined,
    # so one with a single child returns that child's own type (Point, not MultiPoint).
    if len(elements) == 1 and _local(elements[0].tag) != "MultiGeometry":
        return _simple_geometries(elements[0], budget)[0]
    return _combine([g for el in elements for g in _simple_geometries(el, budget)])


def _properties(placemark: Element) -> dict[str, str]:
    props: dict[str, str] = {}
    for tag in ("name", "description"):
        value = _text(next(iter(_children(placemark, tag)), None))
        if value is not None:
            props[tag] = value
    for extended in _children(placemark, "ExtendedData"):
        for el in extended.iter():
            key = el.get("name")
            if not key:
                continue
            if _local(el.tag) == "Data":
                value_nodes = _children(el, "value")
                props.setdefault(key, _text(value_nodes[0]) or "" if value_nodes else "")
            elif _local(el.tag) == "SimpleData":
                props.setdefault(key, (el.text or "").strip())
    return props


def _parse_xml(data: bytes, max_depth: int, max_elements: int) -> Element:
    """Parse safely, aborting as soon as nesting passes ``max_depth`` (root is depth 1) or the
    number of elements passes ``max_elements``."""
    root, depth, elements = None, 0, 0
    try:
        for event, el in safe_et.iterparse(
            io.BytesIO(data), events=("start", "end"), forbid_dtd=True
        ):
            if event == "end":
                depth -= 1
                continue
            depth += 1
            elements += 1
            if root is None:
                root = el
            if depth > max_depth:
                raise ParseError(f"XML is nested more than {max_depth} levels deep")
            if elements > max_elements:
                raise ParseError(f"KML has more than {max_elements} XML elements")
    except ParseError:
        raise
    except (
        safe_et.ParseError,
        DefusedXmlException,
        ValueError,
        LookupError,  # unknown encoding named in the XML declaration
        RecursionError,
    ) as exc:
        raise ParseError(f"not a readable KML (XML) file: {exc}") from exc
    if root is None:
        raise ParseError("not a readable KML (XML) file: no root element")
    return root


def parse_kml(
    data: bytes,
    *,
    max_features: int = MAX_FEATURES,
    max_vertices: int = MAX_VERTICES,
    max_parts: int = MAX_PARTS,
    max_depth: int = MAX_XML_DEPTH,
    max_elements: int = MAX_XML_ELEMENTS,
) -> ParsedFile:
    if not data.strip():
        raise ParseError("file is empty")
    root = _parse_xml(data, max_depth, max_elements)
    if _local(root.tag) != "kml":
        raise NotKmlError(f"not a KML file: root element is <{_local(root.tag)}>, expected <kml>")

    features: list[Feature] = []
    warnings: list[str] = []
    budget = Budget(max_vertices, "vertices")
    parts = Budget(max_parts, "parts")
    for el in root.iter():
        if _local(el.tag) != "Placemark":
            continue
        index = len(features)
        if index >= max_features:
            raise ParseError(f"file has more than {max_features} features")
        problem = None
        try:
            # Every line and every polygon ring is a <LineString> or <LinearRing>; count them
            # all before any geometry of this Placemark is built.
            parts.take(sum(1 for e in el.iter() if _local(e.tag) in _PART_TAGS))
            geometry = _placemark_geometry(el, budget)
        except _GeometryError as exc:
            geometry = None
            problem = f"{exc}; geometry skipped"
            warnings.append(f"Placemark {index}: {problem}")
        features.append(
            Feature(
                index=index,
                source_id=el.get("id"),
                geometry=geometry,
                properties=_properties(el),
                warnings=(problem,) if problem else (),
            )
        )
    return ParsedFile(crs=WGS84, features=features, warnings=warnings)
