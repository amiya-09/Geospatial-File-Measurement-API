"""The committed samples are exactly what scripts/make_samples.py generates, and they behave
as the smoke script expects."""

from pathlib import Path

import pytest

from scripts import make_samples
from tests.api_helpers import upload

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def test_committed_samples_match_the_generator(tmp_path):
    make_samples.generate(tmp_path)
    generated = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    committed = {p.name: p.read_bytes() for p in SAMPLES.iterdir() if p.is_file()}
    assert generated == committed


def test_generation_is_deterministic(tmp_path):
    make_samples.generate(tmp_path / "a")
    make_samples.generate(tmp_path / "b")
    for path in (tmp_path / "a").iterdir():
        assert path.read_bytes() == (tmp_path / "b" / path.name).read_bytes()


@pytest.mark.parametrize(("name", "status"), sorted(make_samples.EXPECTED_STATUS.items()))
def test_every_sample_gets_its_expected_status(client, name, status):
    resp = upload(client, (SAMPLES / name).read_bytes(), name)
    assert resp.status_code == status, resp.text


def test_sample_kml_covers_point_line_polygon_with_hole_and_multigeometry(client):
    fid = upload(client, (SAMPLES / "sample.kml").read_bytes(), "sample.kml").json()["id"]
    features = client.get(f"/api/files/{fid}/measurements/").json()["features"]
    types = [f["geometry_type"] for f in features]
    assert {"Point", "LineString", "Polygon"} <= set(types)
    assert any(t.startswith("Multi") or t == "GeometryCollection" for t in types)
    polygon = next(f for f in features if f["geometry_type"] == "Polygon")
    assert len(polygon["geometry"]["coordinates"]) == 2  # outer ring + hole


def test_sample_shapefile_is_utm_and_measures_in_metres(client):
    data = (SAMPLES / "sample_shapefile.zip").read_bytes()
    info = upload(client, data, "sample_shapefile.zip").json()
    assert info["crs"].startswith("EPSG:326")
    feature = client.get(f"/api/files/{info['id']}/measurements/").json()["features"][0]
    assert feature["measurement"]["area_unit"] == "m2" and feature["measurement"]["area_m2"] > 0
