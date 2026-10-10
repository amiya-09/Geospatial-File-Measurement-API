"""Hostile and corrupt uploads through the real HTTP layer: every response is a success or a
defined 4xx in the one error shape, never a 500."""

import random

import pytest

from app import config
from tests.api_helpers import SURVEY_KML, upload, utm_polygon_zip
from tests.fixtures import make_zip, shapefile_zip
from tests.test_kml_content import kml, nested
from tests.test_parser_totality import (
    KML,
    MUTATIONS,
    SEED,
    SHAPEFILES,
    WGS84_PRJ,
    mutate,
)

DEFINED = {200, 201, 400, 413, 415, 422}


def assert_defined(resp, label=""):
    assert resp.status_code in DEFINED, (label, resp.status_code, resp.text[:200])
    if resp.status_code >= 400:
        err = resp.json()["error"]
        assert isinstance(err["code"], str) and isinstance(err["message"], str), label
        assert err["code"] != "internal_error", label


@pytest.fixture
def parts():
    return SHAPEFILES["polygons"]


def test_zip_slip_names_are_400(client, parts):
    for name in ("../evil.shp", "..\\evil.dbf", "/etc/passwd", "C:\\evil.shx"):
        resp = upload(client, shapefile_zip(parts, extra={name: b"x"}))
        assert resp.status_code == 400, name
        assert "unsafe" in resp.json()["error"]["message"]


def test_zip_bomb_is_400_and_does_not_get_decompressed(client, parts):
    bomb = shapefile_zip(parts, extra={"zeros.bin": b"\x00" * 300_000_000})
    assert len(bomb) < config.MAX_UPLOAD_BYTES
    resp = upload(client, bomb)
    assert resp.status_code == 400 and "uncompressed" in resp.json()["error"]["message"]


def test_zip_just_over_100_mb_uncompressed_is_400(client, parts):
    zipped = shapefile_zip(parts, extra={"zeros.bin": b"\x00" * (100 * 1024 * 1024 + 1)})
    resp = upload(client, zipped)
    assert resp.status_code == 400 and "uncompressed" in resp.json()["error"]["message"]


def test_encrypted_and_multi_shapefile_zips_are_400(client, parts):
    from tests.fixtures import mark_entries_encrypted

    assert upload(client, mark_entries_encrypted(shapefile_zip(parts))).status_code == 400
    two = shapefile_zip(
        parts, extra={"b.shp": parts["shp"], "b.shx": parts["shx"], "b.dbf": parts["dbf"]}
    )
    assert upload(client, two).status_code == 400


def test_forged_small_zip_header_is_400_not_500(client, parts):
    from tests.fixtures import forge_uncompressed_size

    zipped = forge_uncompressed_size(
        shapefile_zip(parts, extra={"big.bin": b"\x00" * 10_000_000}), 10
    )
    assert_defined(upload(client, zipped))


@pytest.mark.parametrize("tag", ["Folder", "MultiGeometry"])
def test_deeply_nested_kml_is_400(client, tag):
    inner = "<Placemark><Point><coordinates>1,1</coordinates></Point></Placemark>"
    resp = upload(client, kml(nested(tag, 20_000, inner)))
    assert resp.status_code == 400 and "nested" in resp.json()["error"]["message"]


def test_xml_entity_bomb_and_external_entity_are_400(client):
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
        b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><kml><Document><name>&c;</name>'
        b"</Document></kml>"
    )
    xxe = (
        b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b"<kml><Document><name>&x;</name></Document></kml>"
    )
    for data in (bomb, xxe):
        resp = upload(client, data)
        assert resp.status_code == 400
        assert "root:" not in resp.text


def test_too_many_vertices_in_kml_is_400(client, monkeypatch):
    from app.services import files as files_service

    original = files_service.parse_kml
    monkeypatch.setattr(files_service, "parse_kml", lambda data: original(data, max_vertices=10))
    coords = " ".join(f"{i / 100},0" for i in range(50))
    data = kml(
        f"<Placemark><LineString><coordinates>{coords}</coordinates></LineString></Placemark>"
    )
    resp = upload(client, data)
    assert resp.status_code == 400 and "vertices" in resp.json()["error"]["message"]


def test_mutated_kml_through_the_api_is_never_a_500(client):
    rng = random.Random(SEED + 10)
    for i in range(min(MUTATIONS, 150)):
        assert_defined(upload(client, mutate(KML, rng), "m.kml"), f"kml i={i}")


@pytest.mark.filterwarnings("ignore::shapefile.PossiblyCorruptFileHeader")
@pytest.mark.filterwarnings("ignore::shapefile.PossibleDataLoss")
def test_mutated_shapefile_zips_through_the_api_are_never_a_500(client):
    rng = random.Random(SEED + 11)
    base = shapefile_zip(SHAPEFILES["polygons"], prj=WGS84_PRJ, cpg="UTF-8")
    for i in range(min(MUTATIONS, 150)):
        assert_defined(upload(client, mutate(base, rng), "m.zip"), f"zip i={i}")


@pytest.mark.filterwarnings("ignore::shapefile.PossiblyCorruptFileHeader")
@pytest.mark.filterwarnings("ignore::shapefile.PossibleDataLoss")
def test_mutated_members_inside_a_valid_zip_are_never_a_500(client):
    rng = random.Random(SEED + 12)
    names = list(SHAPEFILES)
    for i in range(min(MUTATIONS, 150)):
        members = {**SHAPEFILES[names[i % len(names)]], "prj": WGS84_PRJ.encode(), "cpg": b"UTF-8"}
        victim = rng.choice(sorted(members))
        members[victim] = mutate(members[victim], rng) if members[victim] else b"x"
        assert_defined(
            upload(client, make_zip({f"d.{k}": v for k, v in members.items()})), f"i={i}"
        )


def test_successful_hostile_looking_uploads_stay_readable(client):
    for data in (SURVEY_KML, utm_polygon_zip()):
        fid = upload(client, data).json()["id"]
        assert client.get(f"/api/files/{fid}/measurements/").status_code == 200


def test_cpg_naming_base64_is_not_a_500(client):
    resp = upload(client, shapefile_zip(SHAPEFILES["points"], prj=WGS84_PRJ, cpg="base64"))
    assert resp.status_code == 201
    assert any(".cpg" in w for w in resp.json()["warnings"])
