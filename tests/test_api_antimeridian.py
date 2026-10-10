"""Antimeridian handling end to end through the API (upload, store, read back)."""

import pytest
from pyproj import Geod

from tests.api_helpers import placemark, polygon_kml, upload
from tests.test_kml_content import kml

GEOD = Geod(ellps="WGS84")
TRUE_BOX_M2 = abs(GEOD.polygon_area_perimeter([179, 181, 181, 179], [-1, -1, 1, 1])[0])
TRUE_LINE_M = GEOD.line_length([179, -179], [0, 0])


def measurements(client, data):
    resp = upload(client, data)
    assert resp.status_code == 201, resp.text
    body = client.get(f"/api/files/{resp.json()['id']}/measurements/").json()
    return resp.json(), body["features"]


def test_reference_values_are_what_the_task_expects():
    assert TRUE_BOX_M2 == pytest.approx(4.9e10, rel=0.01)
    assert TRUE_LINE_M == pytest.approx(222_000, rel=0.01)


def test_wrapped_two_degree_box_has_about_4_9e10_m2(client):
    box = placemark("box", polygon_kml("179,-1 -179,-1 -179,1 179,1 179,-1"))
    info, (feature,) = measurements(client, kml(box))
    assert feature["measurement"]["area_m2"] == pytest.approx(TRUE_BOX_M2, rel=0.001)
    assert feature["measurement"]["area_m2"] == pytest.approx(4.9e10, rel=0.01)
    assert any("antimeridian" in w for w in feature["warnings"] + info["warnings"])


def test_wrapped_equator_line_is_about_222_km(client):
    line = placemark("eq", "<LineString><coordinates>179,0 -179,0</coordinates></LineString>")
    _, (feature,) = measurements(client, kml(line))
    assert feature["measurement"]["length_m"] == pytest.approx(TRUE_LINE_M, rel=0.001)
    assert feature["measurement"]["length_m"] == pytest.approx(222_000, rel=0.01)
    assert feature["measurement"]["length_unit"] == "m"


def test_pre_split_multipolygon_is_summed_without_a_warning(client):
    multi = (
        "<MultiGeometry>"
        + polygon_kml("179,-1 180,-1 180,1 179,1 179,-1")
        + polygon_kml("-180,-1 -179,-1 -179,1 -180,1 -180,-1")
        + "</MultiGeometry>"
    )
    info, (feature,) = measurements(client, kml(placemark("split", multi)))
    assert feature["geometry_type"] == "MultiPolygon"
    assert feature["measurement"]["area_m2"] == pytest.approx(TRUE_BOX_M2, rel=0.001)
    assert not any("antimeridian" in w for w in feature["warnings"] + info["warnings"])
