import pytest
import shapefile  # noqa: E402  (grouped with the helpers above on purpose)
from pyproj import Geod
from sqlalchemy import func, select

from app import config
from app.db.models import FeatureRecord, FileRecord
from tests import expected_values as ev
from tests.api_helpers import (
    SURVEY_KML,
    WGS84_PRJ,
    is_uuid,
    placemark,
    polygon_kml,
    upload,
    utm_polygon_zip,
)
from tests.fixtures import shapefile_parts, shapefile_zip
from tests.test_kml_content import kml

INFO_KEYS = {"id", "filename", "format", "feature_count", "crs", "status", "warnings", "error"}


def row_counts(session_factory):
    with session_factory() as session:
        return (
            session.scalar(select(func.count()).select_from(FileRecord)),
            session.scalar(select(func.count()).select_from(FeatureRecord)),
        )


# ---- success: KML ----


def test_kml_upload_then_info_then_measurements(client):
    resp = upload(client, SURVEY_KML, "survey.kml")
    assert resp.status_code == 201
    info = resp.json()
    assert set(info) == INFO_KEYS
    assert is_uuid(info["id"])
    assert (info["filename"], info["format"], info["feature_count"]) == ("survey.kml", "kml", 4)
    assert (info["crs"], info["status"], info["error"]) == ("EPSG:4326", "COMPLETED", None)
    assert any("Placemark 3" in w for w in info["warnings"])  # the placemark without geometry

    got = client.get(f"/api/files/{info['id']}/", follow_redirects=False)
    assert got.status_code == 200 and got.json() == info  # POST and GET info share one shape

    resp = client.get(f"/api/files/{info['id']}/measurements/", follow_redirects=False)
    assert resp.status_code == 200
    body = resp.json()
    assert body["file_id"] == info["id"] and body["crs"] == "EPSG:4326"
    features = body["features"]
    assert [f["index"] for f in features] == [0, 1, 2, 3]  # stable, in file order
    pin, road, parcel, nothing = features

    assert pin["geometry_type"] == "Point" and pin["crs"] == "EPSG:4326"
    assert pin["geometry"] == {"type": "Point", "coordinates": [10.5, 50.25]}  # altitude dropped
    assert pin["properties"] == {"name": "pin"}
    assert pin["measurement"]["reason"] == "not applicable"
    assert pin["measurement"]["area_m2"] is None and pin["measurement"]["length_m"] is None
    assert pin["is_valid"] is True and pin["validity_reason"] is None and pin["warnings"] == []

    m = road["measurement"]
    assert m["length_m"] == pytest.approx(ev.LINE_EQUATOR_1DEG["length_m"], rel=0.001)
    assert m["length_unit"] == "m" and m["length_crs"].startswith("+proj=aeqd")
    assert m["area_m2"] is None and m["area_unit"] is None and m["reason"] is None

    m = parcel["measurement"]
    assert m["area_m2"] == pytest.approx(ev.POLYGON_WITH_HOLE["area_m2"], rel=0.001)
    assert m["area_unit"] == "m2" and m["area_crs"].startswith("+proj=laea")
    assert parcel["geometry"]["type"] == "Polygon" and len(parcel["geometry"]["coordinates"]) == 2

    assert nothing["geometry"] is None and nothing["geometry_type"] is None
    assert nothing["measurement"]["reason"] == "no geometry"
    assert nothing["properties"] == {"name": "no geometry"}


def test_include_geometry_false_omits_only_the_geometry(client):
    fid = upload(client, SURVEY_KML).json()["id"]
    full = client.get(f"/api/files/{fid}/measurements/").json()["features"]
    slim = client.get(f"/api/files/{fid}/measurements/?include_geometry=false").json()["features"]
    assert all(f["geometry"] is None for f in slim)
    for a, b in zip(full, slim, strict=True):
        assert {**a, "geometry": None} == b


def test_invalid_polygon_is_flagged_and_still_measured(client):
    bowtie = placemark("bowtie", polygon_kml("0,0 1,1 1,0 0,1 0,0"))
    fid = upload(client, kml(bowtie)).json()["id"]
    feature = client.get(f"/api/files/{fid}/measurements/").json()["features"][0]
    assert feature["is_valid"] is False
    assert feature["validity_reason"].startswith("Self-intersection")
    assert feature["measurement"]["area_m2"] > 0


def test_antimeridian_warning_reaches_the_feature_and_the_file(client):
    wrapped = placemark("wrapped", polygon_kml("179,0 179,1 -179,1 -179,0 179,0"))
    info = upload(client, kml(wrapped)).json()
    assert any("feature 0" in w and "antimeridian" in w for w in info["warnings"])
    feature = client.get(f"/api/files/{info['id']}/measurements/").json()["features"][0]
    assert any("antimeridian" in w for w in feature["warnings"])
    lons, lats = [179, 179, 181, 181], [0, 1, 1, 0]
    truth = abs(Geod(ellps="WGS84").polygon_area_perimeter(lons, lats)[0])
    assert feature["measurement"]["area_m2"] == pytest.approx(truth, rel=0.001)


def test_non_finite_property_values_are_stored_as_null(client, monkeypatch):
    from shapely.geometry import Point

    from app.parsers.base import Feature, ParsedFile
    from app.parsers.kml import WGS84
    from app.services import files as files_service

    def fake_parse_kml(data):
        feature = Feature(
            0, None, Point(1, 1), {"ok": 1.5, "bad": float("nan"), "inf": float("inf")}
        )
        return ParsedFile(crs=WGS84, features=[feature])

    monkeypatch.setattr(files_service, "parse_kml", fake_parse_kml)
    fid = upload(client, kml()).json()["id"]
    props = client.get(f"/api/files/{fid}/measurements/").json()["features"][0]["properties"]
    assert props == {"ok": 1.5, "bad": None, "inf": None}


# ---- success: zipped shapefile ----


def test_zipped_shapefile_in_utm_is_reprojected_to_wgs84_in_the_response(client):
    info = upload(client, utm_polygon_zip(), "boxes.zip").json()
    assert (info["format"], info["crs"], info["feature_count"]) == ("shapefile", "EPSG:32632", 2)
    body = client.get(f"/api/files/{info['id']}/measurements/").json()
    box, hole = body["features"]
    assert [box["index"], hole["index"]] == [0, 1]
    assert box["crs"] == "EPSG:32632"  # the source CRS, while the geometry is WGS84
    ring = box["geometry"]["coordinates"][0]
    assert all(8.99 < lon < 9.11 and 44.99 < lat < 45.11 for lon, lat in ring)
    lons, lats = [9, 9.1, 9.1, 9], [45, 45, 45.1, 45.1]
    truth = abs(Geod(ellps="WGS84").polygon_area_perimeter(lons, lats)[0])
    assert box["measurement"]["area_m2"] == pytest.approx(truth, rel=0.001)
    assert box["measurement"]["area_unit"] == "m2"
    assert hole["geometry"] is None and hole["measurement"]["reason"] == "no geometry"
    assert any("null shape" in w for w in info["warnings"])


def test_shapefile_without_prj_but_with_lon_lat_coordinates_succeeds_with_a_warning(client):
    parts = shapefile_parts(lambda w: (w.point(10, 50), w.record("p")), shapefile.POINT)
    info = upload(client, shapefile_zip(parts)).json()
    assert info["status"] == "COMPLETED" and info["crs"] == "EPSG:4326"
    assert any(".prj" in w for w in info["warnings"])


# ---- content decides the format, filename is metadata ----


def test_zip_uploaded_with_a_kml_name_and_kml_with_a_zip_name_are_accepted_by_content(client):
    a = upload(client, utm_polygon_zip(), "looks-like.kml")
    b = upload(client, SURVEY_KML, "looks-like.zip")
    c = upload(client, SURVEY_KML, "no-extension")
    assert [r.status_code for r in (a, b, c)] == [201, 201, 201]
    assert [r.json()["format"] for r in (a, b, c)] == ["shapefile", "kml", "kml"]


@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        ("../../etc/passwd.kml", "passwd.kml"),
        ("C:\\Users\\me\\parcel.kml", "parcel.kml"),
        ("dir/sub/a.kml", "a.kml"),
        ("x" * 400 + ".kml", ("x" * 400 + ".kml")[:255]),
    ],
)
def test_filename_is_reduced_to_a_safe_basename_and_capped(client, sent, stored):
    resp = upload(client, SURVEY_KML, sent)
    assert resp.status_code == 201
    assert resp.json()["filename"] == stored
    assert len(resp.json()["filename"]) <= 255


# ---- failures: persistence and status codes ----


def test_unmeasurable_shapefile_is_422_and_leaves_a_readable_failed_record(client, session_factory):
    resp = upload(client, utm_polygon_zip(prj=False), "no-prj.zip")
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "unmeasurable_file" and is_uuid(err["file_id"])
    assert ".prj" in err["message"]

    info = client.get(f"/api/files/{err['file_id']}/").json()
    assert set(info) == INFO_KEYS
    assert (info["status"], info["feature_count"], info["format"]) == ("FAILED", 0, "shapefile")
    assert info["error"] == err["message"] and info["filename"] == "no-prj.zip"
    assert row_counts(session_factory) == (1, 0)  # a file record, no feature rows

    conflict = client.get(f"/api/files/{err['file_id']}/measurements/")
    assert conflict.status_code == 409
    assert conflict.json()["error"] == {"code": "file_failed", "message": err["message"]}


@pytest.mark.parametrize("coords", ["200,10", "10,95"])
def test_kml_coordinates_outside_lon_lat_drop_that_geometry_with_a_warning(client, coords):
    data = kml(placemark("far", f"<Point><coordinates>{coords}</coordinates></Point>"))
    resp = upload(client, data, "far.kml")
    assert resp.status_code == 201
    assert any(
        "Placemark 0" in w and "out of the lon/lat range" in w for w in resp.json()["warnings"]
    )
    (feature,) = client.get(f"/api/files/{resp.json()['id']}/measurements/").json()["features"]
    assert feature["geometry"] is None
    assert feature["measurement"]["reason"] == "no geometry"


def test_unknown_id_is_404_on_both_get_routes(client):
    missing = "00000000-0000-4000-8000-000000000000"
    for path in (f"/api/files/{missing}/", f"/api/files/{missing}/measurements/"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


def test_malformed_id_is_a_422_in_the_error_shape(client):
    resp = client.get("/api/files/not-a-uuid/")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_missing_file_field_is_a_422_validation_error(client):
    resp = client.post("/api/files/", data={"other": "x"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_request_without_multipart_is_a_422_validation_error(client):
    resp = client.post(
        "/api/files/", content=b"<kml/>", headers={"content-type": "application/xml"}
    )
    assert resp.status_code == 422


# ---- failures: unreadable and unrecognised content ----


def test_nothing_is_persisted_for_4xx_responses(client, session_factory):
    bad = [
        b"",  # empty
        b"PK\x03\x04" + b"garbage" * 30,  # corrupt zip
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,  # a PNG, whatever its name
        b"<html><body>hi</body></html>",
        SURVEY_KML[:-30],  # truncated KML
    ]
    for data in bad:
        assert upload(client, data, "evil.kml").status_code in (400, 415)
    assert row_counts(session_factory) == (0, 0)


@pytest.mark.parametrize(
    ("data", "status", "code"),
    [
        (b"", 400, "invalid_file"),
        (b"PK\x03\x04" + b"garbage" * 30, 400, "invalid_file"),
        (SURVEY_KML[:-30], 400, "invalid_file"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, 415, "unsupported_media_type"),
        (b"just some text", 415, "unsupported_media_type"),
        (b'{"json": true}', 415, "unsupported_media_type"),
        (b"<html><body>hi</body></html>", 415, "unsupported_media_type"),
    ],
)
def test_error_status_and_code_by_kind_of_bad_content(client, data, status, code):
    resp = upload(client, data, "thing.kml")  # a .kml name must not make any difference
    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code


def test_png_renamed_kml_is_415_and_kml_renamed_png_is_accepted(client):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    assert upload(client, png, "map.kml").status_code == 415
    assert upload(client, SURVEY_KML, "map.png").status_code == 201


# ---- failures: upload size ----


def test_upload_over_the_cap_is_413_and_nothing_is_persisted(client, session_factory, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 2_000)
    resp = upload(client, b"<kml>" + b" " * 5_000 + b"</kml>")
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"
    assert row_counts(session_factory) == (0, 0)


def test_upload_exactly_at_the_cap_is_accepted(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", len(SURVEY_KML))
    assert upload(client, SURVEY_KML).status_code == 201
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", len(SURVEY_KML) - 1)
    assert upload(client, SURVEY_KML).status_code == 413


def test_read_limited_stops_reading_just_past_the_limit():
    from app.errors import ApiError
    from app.services.files import read_limited

    class Endless:
        requested = 0

        def read(self, size=-1):
            self.requested += size
            return b"x" * size

    source = Endless()
    with pytest.raises(ApiError) as caught:
        read_limited(source, limit=100_000, chunk_size=4_096)
    assert caught.value.status_code == 413
    assert source.requested <= 100_000 + 4_096  # never reads on beyond the cap
    assert source.requested > 0


def test_read_limited_returns_everything_when_under_the_limit():
    import io

    from app.services.files import read_limited

    payload = b"abc" * 5_000
    assert read_limited(io.BytesIO(payload), limit=len(payload), chunk_size=1_000) == payload


# ---- transaction ----


def test_a_failure_while_writing_features_leaves_no_file_row(client, session_factory, monkeypatch):
    from sqlalchemy.orm import Session

    def boom(self, instances):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(Session, "add_all", boom)
    resp = upload(client, SURVEY_KML)
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"
    assert "disk on fire" not in resp.text
    assert row_counts(session_factory) == (0, 0)


# ---- routes ----


def test_exact_spec_paths_never_redirect(client):
    fid = upload(client, SURVEY_KML).json()["id"]
    for method, path in [
        ("post", "/api/files/"),
        ("get", f"/api/files/{fid}/"),
        ("get", f"/api/files/{fid}/measurements/"),
    ]:
        kwargs = {"files": {"file": ("a.kml", SURVEY_KML)}} if method == "post" else {}
        resp = getattr(client, method)(path, follow_redirects=False, **kwargs)
        assert resp.status_code != 307 and resp.status_code < 300, (method, path)


def test_paths_without_the_trailing_slash_are_404_not_a_redirect(client):
    fid = upload(client, SURVEY_KML).json()["id"]
    for path in ("/api/files", f"/api/files/{fid}", f"/api/files/{fid}/measurements"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code in (404, 405), path
        assert "location" not in resp.headers


def test_clean_filename_drops_control_characters_and_defaults_when_nothing_is_left():
    from app.services.files import clean_filename

    assert clean_filename("bell\x07\x00.kml") == "bell.kml"  # multipart clients escape these
    assert clean_filename("  \x00 ") == "upload"
    assert clean_filename(None) == "upload"
    assert clean_filename("a/b/") == "upload"


def test_wgs84_prj_constant_is_what_the_helpers_assume():
    assert "WGS 84" in WGS84_PRJ


# ---- optional list endpoint ----


def test_list_files_newest_first_with_paging(client):
    ids = [upload(client, SURVEY_KML, f"f{i}.kml").json()["id"] for i in range(3)]
    resp = client.get("/api/files/", follow_redirects=False)
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["id"] for r in rows] == ids[::-1]
    assert set(rows[0]) == {"id", "filename", "status", "feature_count", "created_at"}
    page = client.get("/api/files/?limit=1&offset=1").json()
    assert [r["id"] for r in page] == [ids[1]]
    assert client.get("/api/files/?limit=0").status_code == 422
    assert client.get("/api/files/").json() != []


def test_list_files_includes_failed_uploads(client):
    upload(client, utm_polygon_zip(prj=False), "bad.zip")
    rows = client.get("/api/files/").json()
    assert [(r["filename"], r["status"], r["feature_count"]) for r in rows] == [
        ("bad.zip", "FAILED", 0)
    ]


def test_created_at_is_utc_iso_8601_with_a_z_suffix(client):
    import re
    from datetime import UTC, datetime, timedelta

    before = datetime.now(UTC) - timedelta(seconds=5)
    upload(client, SURVEY_KML)
    stamp = client.get("/api/files/").json()[0]["created_at"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z", stamp), stamp
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert before <= parsed <= datetime.now(UTC) + timedelta(seconds=5)


# ---- measurements paging ----


def _paged(client, fid, query=""):
    resp = client.get(f"/api/files/{fid}/measurements/{query}", follow_redirects=False)
    return resp


def test_measurements_report_total_and_default_to_everything(client):
    fid = upload(client, SURVEY_KML).json()["id"]
    body = _paged(client, fid).json()
    assert body["total"] == 4 and len(body["features"]) == 4
    assert (body["limit"], body["offset"]) == (None, 0)


def test_measurements_limit_and_offset_page_through_in_index_order(client):
    fid = upload(client, SURVEY_KML).json()["id"]
    first = _paged(client, fid, "?limit=2").json()
    second = _paged(client, fid, "?limit=2&offset=2").json()
    assert [f["index"] for f in first["features"]] == [0, 1]
    assert [f["index"] for f in second["features"]] == [2, 3]
    assert first["total"] == second["total"] == 4
    assert (second["limit"], second["offset"]) == (2, 2)
    assert _paged(client, fid, "?offset=3").json()["features"][0]["index"] == 3
    past_end = _paged(client, fid, "?offset=10").json()
    assert past_end["features"] == [] and past_end["total"] == 4


def test_measurements_paging_combines_with_include_geometry(client):
    fid = upload(client, SURVEY_KML).json()["id"]
    page = _paged(client, fid, "?limit=1&offset=2&include_geometry=false").json()
    assert [f["index"] for f in page["features"]] == [2]
    assert page["features"][0]["geometry"] is None


@pytest.mark.parametrize(
    "query", ["?limit=0", "?limit=1001", "?limit=-1", "?offset=-1", "?limit=x"]
)
def test_measurements_paging_bounds_are_validated(client, query):
    fid = upload(client, SURVEY_KML).json()["id"]
    resp = _paged(client, fid, query)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "validation_error"


def test_measurements_limit_1000_is_accepted(client):
    fid = upload(client, SURVEY_KML).json()["id"]
    assert _paged(client, fid, "?limit=1000").status_code == 200


# SQLite integers are signed 64 bit. Found by schemathesis: a larger offset was a 500.
SQLITE_MAX_INT = 2**63 - 1


def test_offset_up_to_the_sqlite_integer_limit_is_an_empty_page(client):
    fid = upload(client, SURVEY_KML).json()["id"]
    assert client.get(f"/api/files/?offset={SQLITE_MAX_INT}").json() == []
    assert _paged(client, fid, f"?offset={SQLITE_MAX_INT}").json()["features"] == []


def test_offset_beyond_the_sqlite_integer_limit_is_422_not_500(client):
    fid = upload(client, SURVEY_KML).json()["id"]
    for resp in (
        client.get(f"/api/files/?offset={SQLITE_MAX_INT + 1}"),
        _paged(client, fid, f"?offset={SQLITE_MAX_INT + 1}"),
    ):
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


# ---- unsupported geometry is handled gracefully (assignment section 3) ----


def test_unsupported_kml_geometry_is_kept_without_geometry_and_never_a_500(client):
    track = placemark("gps track", "<gx:Track><when>2026-01-01T00:00:00Z</when></gx:Track>")
    data = kml(track + placemark("pin", "<Point><coordinates>1,1</coordinates></Point>")).replace(
        b"<kml ", b'<kml xmlns:gx="http://www.google.com/kml/ext/2.2" ', 1
    )
    resp = upload(client, data)
    assert resp.status_code == 201
    assert any("Placemark 0" in w for w in resp.json()["warnings"])
    first, second = client.get(f"/api/files/{resp.json()['id']}/measurements/").json()["features"]
    assert first["geometry"] is None and first["measurement"]["reason"] == "no geometry"
    assert second["geometry_type"] == "Point" and second["index"] == 1


def test_geometry_collection_of_mixed_types_is_measured_by_type(client):
    mixed = (
        "<MultiGeometry><Point><coordinates>0,0</coordinates></Point>"
        "<LineString><coordinates>0,0 1,0</coordinates></LineString>"
        + polygon_kml("0,0 1,0 1,1 0,1 0,0")
        + "</MultiGeometry>"
    )
    fid = upload(client, kml(placemark("mixed", mixed))).json()["id"]
    feature = client.get(f"/api/files/{fid}/measurements/").json()["features"][0]
    assert feature["geometry_type"] == "GeometryCollection"
    assert feature["measurement"]["area_m2"] > 0 and feature["measurement"]["length_m"] > 0


# ---- the 422 for a missing upload says which field is expected ----


@pytest.mark.parametrize(
    "send",
    [
        lambda c: c.post("/api/files/", files={"upload": ("a.kml", b"<kml/>")}),
        lambda c: c.post("/api/files/", data={"other": "x"}),
        lambda c: c.post(
            "/api/files/", content=b"<kml/>", headers={"content-type": "application/xml"}
        ),
    ],
    ids=["wrong field name", "no file part", "not multipart"],
)
def test_missing_file_field_message_names_the_expected_field(client, send):
    resp = send(client)
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    assert 'multipart form field "file"' in error["message"]
    assert '-F "file=@' in error["message"]


def test_other_validation_messages_are_unchanged(client):
    resp = client.get("/api/files/not-a-uuid/")
    assert resp.json()["error"]["message"].startswith("path.file_id: ")


def test_parts_created_by_repair_count_towards_the_upload_part_limit(client, monkeypatch):
    # A sawtooth whose teeth cross the bottom edge: one ring in the file, many parts after
    # repair. The parser's cap counts the ring; the measured part count is checked as well.
    monkeypatch.setattr(config, "MAX_PARTS", 5)
    teeth = " ".join(f"{i / 10},{0.1 if i % 2 else -0.1}" for i in range(12))
    ring = f"{teeth} 1.2,-0.05 0,-0.05 0,-0.1"
    resp = upload(client, kml(placemark("saw", polygon_kml(ring))))
    assert resp.status_code == 422
    assert "more than 5 parts" in resp.json()["error"]["message"]
