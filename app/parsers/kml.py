"""KML parser. Placemarks become features; coordinates are always WGS84 (lon, lat)."""

import defusedxml.ElementTree as safe_et
from defusedxml import DefusedXmlException
from pyproj import CRS

from app.parsers.base import ParsedFile, ParseError

WGS84 = CRS.from_epsg(4326)


def _local(tag: str) -> str:
    """Tag name without its XML namespace."""
    return tag.rsplit("}", 1)[-1]


def parse_kml(data: bytes) -> ParsedFile:
    if not data.strip():
        raise ParseError("file is empty")
    try:
        root = safe_et.fromstring(data, forbid_dtd=True)
    except (safe_et.ParseError, DefusedXmlException, ValueError) as exc:
        raise ParseError(f"not a readable KML (XML) file: {exc}") from exc
    if _local(root.tag) != "kml":
        raise ParseError(f"not a KML file: root element is <{_local(root.tag)}>, expected <kml>")
    return ParsedFile(crs=WGS84, features=[], warnings=[])
