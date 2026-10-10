"""Shared helpers for API tests."""

import uuid

import shapefile
from pyproj import CRS, Transformer

from tests.fixtures import shapefile_parts, shapefile_zip
from tests.test_kml_content import kml

WGS84_PRJ = CRS.from_epsg(4326).to_wkt()


def upload(client, data: bytes, filename: str = "upload.bin", **kwargs):
    return client.post(
        "/api/files/",
        files={"file": (filename, data, "application/octet-stream")},
        follow_redirects=False,
        **kwargs,
    )


def is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def placemark(name: str, geometry: str = "") -> str:
    return f"<Placemark><name>{name}</name>{geometry}</Placemark>"


def polygon_kml(ring: str, hole: str | None = None) -> str:
    inner = (
        f"<innerBoundaryIs><LinearRing><coordinates>{hole}</coordinates></LinearRing>"
        "</innerBoundaryIs>"
        if hole
        else ""
    )
    return (
        "<Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{ring}</coordinates></LinearRing></outerBoundaryIs>{inner}</Polygon>"
    )


SURVEY_KML = kml(
    placemark("pin", "<Point><coordinates>10.5,50.25,120</coordinates></Point>")
    + placemark("road", "<LineString><coordinates>0,0 1,0</coordinates></LineString>")
    + placemark(
        "parcel",
        polygon_kml(
            "0,0 1,0 1,1 0,1 0,0",
            "0.4,0.4 0.6,0.4 0.6,0.6 0.4,0.6 0.4,0.4",
        ),
    )
    + placemark("no geometry")
)


def utm_polygon_zip(prj: bool = True) -> bytes:
    """A 0.1 degree box near (9E, 45N) written in EPSG:32632 metres."""
    utm = CRS.from_epsg(32632)
    fwd = Transformer.from_crs(4326, utm, always_xy=True)
    corners = [fwd.transform(x, y) for x, y in [(9, 45), (9.1, 45), (9.1, 45.1), (9, 45.1)]]
    ring = [corners[0], corners[3], corners[2], corners[1], corners[0]]  # clockwise

    def build(w):
        w.poly([ring])
        w.record("box")
        w.null()
        w.record("hole in the data")

    parts = shapefile_parts(build, shapefile.POLYGON)
    return shapefile_zip(parts, prj=utm.to_wkt() if prj else None)
