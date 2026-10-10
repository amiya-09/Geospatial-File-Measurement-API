"""Longitude must be within [-180, 180] and latitude within [-90, 90], with no tolerance.

A latitude a hair above 90 used to pass a 1e-6 degree tolerance, project to infinity and
crash the database insert with a 500. Now the parsers drop such a geometry with a warning,
and measure() refuses it. A file keeps the good features (partial success), but a file where
no feature is left with a usable geometry is unmeasurable (422 with a FAILED record).
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


BAD_POLYGON = polygon_kml(f"0,89 1,89 1,{JUST_OVER} 0,{JUST_OVER} 0,89")
GOOD_POINT = "<Point><coordinates>1,1</coordinates></Point>"


def assert_unusable_422(client, resp, fmt):
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "unmeasurable_file"
    assert "has a usable geometry" in err["message"]
    assert "out of range or unusable" in err["message"]
    info = client.get(f"/api/files/{err['file_id']}/").json()
    assert info["status"] == "FAILED" and info["format"] == fmt
    assert client.get(f"/api/files/{err['file_id']}/measurements/").status_code == 409


def test_kml_with_one_bad_and_one_good_feature_is_a_partial_success(client):
    resp = upload(client, kml(placemark("bad", BAD_POLYGON) + placemark("ok", GOOD_POINT)))
    assert resp.status_code == 201
    assert resp.json()["feature_count"] == 2
    assert any("out of the lon/lat range" in w for w in resp.json()["warnings"])


def test_kml_where_every_feature_is_out_of_range_is_422_with_a_failed_record(client):
    resp = upload(client, kml(placemark("bad", BAD_POLYGON) + placemark("far", BAD_POLYGON)))
    assert_unusable_422(client, resp, "kml")
    assert "Placemark 0" in resp.json()["error"]["message"]  # the first warning says which


def test_kml_whose_placemarks_have_no_geometry_at_all_is_422(client):
    assert_unusable_422(client, upload(client, kml(placemark("a") + placemark("b"))), "kml")


def test_kml_with_no_placemarks_is_still_an_empty_success(client):
    resp = upload(client, kml(""))
    assert resp.status_code == 201 and resp.json()["feature_count"] == 0


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


def test_shapefile_with_one_bad_and_one_good_feature_is_a_partial_success(client):
    resp = upload(client, point_zip(JUST_OVER, WGS84_PRJ))
    assert resp.status_code == 201
    assert resp.json()["feature_count"] == 2


def test_shapefile_where_every_feature_is_out_of_range_is_422_with_a_failed_record(client):
    def build(w):
        for y in (JUST_OVER, -JUST_OVER):
            w.point(1, y)
            w.record("bad")

    zipped = shapefile_zip(shapefile_parts(build, shapefile.POINT), prj=WGS84_PRJ)
    assert_unusable_422(client, upload(client, zipped), "shapefile")


def test_shapefile_without_prj_and_out_of_range_coordinates_is_still_unmeasurable():
    with pytest.raises(UnmeasurableError, match="outside the lon/lat range"):
        parse_shapefile_zip(point_zip(JUST_OVER, None))
