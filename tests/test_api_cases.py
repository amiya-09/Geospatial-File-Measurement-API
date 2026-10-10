"""API status codes and bodies for cases the other tests check only below the API."""

import pytest
import shapefile

from tests.api_helpers import SURVEY_KML, WGS84_PRJ, placemark, upload
from tests.fixtures import shapefile_parts, shapefile_zip
from tests.test_api_files import row_counts
from tests.test_kml_content import kml

MEASUREMENT_VALUES = ("area_m2", "length_m", "area_unit", "length_unit", "area_crs", "length_crs")


def point_parts() -> dict[str, bytes]:
    return shapefile_parts(lambda w: (w.point(10, 50), w.record("p")), shapefile.POINT)


@pytest.mark.parametrize("missing", ["shp", "shx", "dbf"])
def test_zip_missing_a_required_member_is_400_and_stores_nothing(client, session_factory, missing):
    parts = {ext: data for ext, data in point_parts().items() if ext != missing}
    resp = upload(client, shapefile_zip(parts, prj=WGS84_PRJ), "partial.zip")
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "invalid_file" and missing in error["message"]
    assert row_counts(session_factory) == (0, 0)


@pytest.mark.parametrize("prj", ["", "not wkt at all"])
def test_undefined_prj_is_422_with_a_failed_record(client, prj):
    resp = upload(client, shapefile_zip(point_parts(), prj=prj), "bad-prj.zip")
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "unmeasurable_file" and ".prj" in error["message"]
    info = client.get(f"/api/files/{error['file_id']}/").json()
    assert (info["status"], info["feature_count"]) == ("FAILED", 0)


def test_malformed_id_on_the_measurements_route_is_a_422_in_the_error_shape(client):
    resp = client.get("/api/files/not-a-uuid/measurements/")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("query", ["?limit=201", "?limit=-1", "?offset=-1", "?limit=x"])
def test_list_paging_bounds_are_validated(client, query):
    resp = client.get(f"/api/files/{query}")
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "validation_error"


def test_list_limit_200_is_accepted(client):
    upload(client, SURVEY_KML)
    resp = client.get("/api/files/?limit=200")
    assert resp.status_code == 200 and len(resp.json()) == 1


def test_multipoint_and_unknown_geometry_get_null_values_and_a_reason_never_a_number(client):
    stops = (
        "<MultiGeometry><Point><coordinates>1,1</coordinates></Point>"
        "<Point><coordinates>2,2</coordinates></Point></MultiGeometry>"
    )
    track = "<gx:Track><when>2026-01-01T00:00:00Z</when></gx:Track>"
    data = kml(placemark("stops", stops) + placemark("gps track", track)).replace(
        b"<kml ", b'<kml xmlns:gx="http://www.google.com/kml/ext/2.2" ', 1
    )
    resp = upload(client, data)
    assert resp.status_code == 201
    first, second = client.get(f"/api/files/{resp.json()['id']}/measurements/").json()["features"]
    assert first["geometry_type"] == "MultiPoint"
    assert first["measurement"]["reason"] == "not applicable"
    assert second["geometry"] is None and second["measurement"]["reason"] == "no geometry"
    for feature in (first, second):
        assert all(feature["measurement"][key] is None for key in MEASUREMENT_VALUES)
