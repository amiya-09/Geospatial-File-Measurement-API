"""Longitude must be within [-180, 180] and latitude within [-90, 90], with no tolerance.

A latitude a hair above 90 used to pass a 1e-6 degree tolerance, project to infinity and
crash the database insert with a 500. Now the parsers drop such a geometry with a warning,
and measure() refuses it.
"""

import pytest
import shapefile
from pyproj import CRS
from shapely.geometry import LineString, Polygon

from app.geo.measure import MeasurementError, measure
from app.parsers.base import UnmeasurableError
from app.parsers.kml import parse_kml
from app.parsers.shapefile import parse_shapefile_zip
from tests.api_helpers import placemark, polygon_kml, upload
from tests.fixtures import shapefile_parts, shapefile_zip
from tests.test_kml_content import kml

WGS84_PRJ = CRS.from_epsg(4326).to_wkt()
JUST_OVER = 90.0000001


@pytest.mark.parametrize(
    "geom",
    [
        Polygon([(0, 89), (1, 89), (1, JUST_OVER), (0, JUST_OVER)]),
        LineString([(0, 89), (0, -JUST_OVER)]),
        LineString([(180.0000001, 0), (179, 0)]),
    ],
    ids=["lat_above_90", "lat_below_minus_90", "lon_above_180"],
)
def test_measure_rejects_coordinates_just_outside_the_range(geom):
    with pytest.raises(MeasurementError, match="not valid lon/lat"):
        measure(geom)


def test_coordinates_exactly_on_the_limits_are_accepted():
    m = measure(Polygon([(179, 89), (180, 89), (180, 90), (179, 90)]))
    assert m.area_m2 > 0


@pytest.mark.parametrize("coords", [f"0,89 1,89 1,{JUST_OVER} 0,89", "180.0000001,0 179,0"])
def test_kml_drops_an_out_of_range_geometry_with_a_warning(coords):
    tag = "Polygon" if " 0,89" in coords else "LineString"
    if tag == "Polygon":
        geometry = polygon_kml(coords)
    else:
        geometry = f"<LineString><coordinates>{coords}</coordinates></LineString>"
    ok = "<Point><coordinates>1,1</coordinates></Point>"
    parsed = parse_kml(kml(placemark("bad", geometry) + placemark("ok", ok)))
    assert parsed.features[0].geometry is None
    assert parsed.features[1].geometry is not None
    assert len(parsed.warnings) == 1 and "out of the lon/lat range" in parsed.warnings[0]


def test_kml_upload_with_latitude_just_over_90_is_not_a_500(client):
    body = kml(placemark("bad", polygon_kml(f"0,89 1,89 1,{JUST_OVER} 0,{JUST_OVER} 0,89")))
    resp = upload(client, body)
    assert resp.status_code == 201
    assert any("out of the lon/lat range" in w for w in resp.json()["warnings"])


def point_zip(y: float, prj: str | None) -> bytes:
    def build(w):
        w.point(1, 1)
        w.record("ok")
        w.point(1, y)
        w.record("bad")

    return shapefile_zip(shapefile_parts(build, shapefile.POINT), prj=prj)


def test_shapefile_with_a_geographic_prj_drops_an_out_of_range_geometry_with_a_warning():
    parsed = parse_shapefile_zip(point_zip(JUST_OVER, WGS84_PRJ))
    assert parsed.features[0].geometry is not None
    assert parsed.features[1].geometry is None
    assert any("feature 1" in w and "out of the lon/lat range" in w for w in parsed.warnings)


def test_shapefile_upload_with_latitude_just_over_90_is_not_a_500(client):
    resp = upload(client, point_zip(JUST_OVER, WGS84_PRJ))
    assert resp.status_code == 201


def test_shapefile_without_prj_and_out_of_range_coordinates_is_still_unmeasurable():
    with pytest.raises(UnmeasurableError, match="outside the lon/lat range"):
        parse_shapefile_zip(point_zip(JUST_OVER, None))
