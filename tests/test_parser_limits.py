import pytest
import shapefile

from app.config import MAX_FEATURES, MAX_VERTICES
from app.parsers import kml as kml_module
from app.parsers import shapefile as shapefile_module
from app.parsers.base import ParseError, VertexBudget
from app.parsers.kml import parse_kml
from app.parsers.shapefile import parse_shapefile_zip
from tests.fixtures import shapefile_parts, shapefile_zip
from tests.test_kml_content import kml


def test_default_limits():
    assert MAX_FEATURES == 10_000
    assert MAX_VERTICES == 1_000_000


def test_vertex_budget_counts_cumulatively_and_allows_exactly_the_limit():
    budget = VertexBudget(5)
    budget.take(3)
    budget.take(2)
    with pytest.raises(ParseError, match="vertices"):
        budget.take(1)


# ---- KML ----


def point_placemarks(n: int) -> str:
    return "".join(
        f"<Placemark><Point><coordinates>{i},0</coordinates></Point></Placemark>" for i in range(n)
    )


def line_placemark(n_points: int) -> str:
    coords = " ".join(f"{i / 10},0" for i in range(n_points))
    return f"<Placemark><LineString><coordinates>{coords}</coordinates></LineString></Placemark>"


def test_kml_feature_cap():
    assert len(parse_kml(kml(point_placemarks(3)), max_features=3).features) == 3
    with pytest.raises(ParseError, match="features"):
        parse_kml(kml(point_placemarks(4)), max_features=3)


def test_kml_vertex_cap_is_cumulative_across_features():
    body = line_placemark(3) + line_placemark(3)
    assert len(parse_kml(kml(body), max_vertices=6).features) == 2
    with pytest.raises(ParseError, match="vertices"):
        parse_kml(kml(body), max_vertices=5)


def test_kml_vertex_cap_applies_to_a_single_large_geometry():
    with pytest.raises(ParseError, match="vertices"):
        parse_kml(kml(line_placemark(50)), max_vertices=10)


def test_kml_limits_are_enforced_before_any_shapely_geometry_is_built(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("a shapely geometry was built before the limit was checked")

    for name in ("Point", "LineString", "Polygon"):
        monkeypatch.setattr(kml_module, name, boom)
    with pytest.raises(ParseError, match="vertices"):
        parse_kml(kml(line_placemark(50)), max_vertices=10)


# ---- Shapefile ----


def point_zip(n: int) -> bytes:
    def build(w):
        for i in range(n):
            w.point(i, 0)
            w.record(f"p{i}")

    return shapefile_zip(shapefile_parts(build, shapefile.POINT), prj=None)


def polygon_zip(n_polygons: int) -> bytes:
    def build(w):
        for i in range(n_polygons):
            w.poly([[(i, 0), (i, 1), (i + 1, 1), (i + 1, 0), (i, 0)]])  # 5 vertices
            w.record(f"g{i}")

    return shapefile_zip(shapefile_parts(build, shapefile.POLYGON), prj=None)


def test_shapefile_feature_cap():
    assert len(parse_shapefile_zip(point_zip(3), max_features=3).features) == 3
    with pytest.raises(ParseError, match="features"):
        parse_shapefile_zip(point_zip(4), max_features=3)


def test_shapefile_vertex_cap_is_cumulative_across_features():
    assert len(parse_shapefile_zip(polygon_zip(2), max_vertices=10).features) == 2
    with pytest.raises(ParseError, match="vertices"):
        parse_shapefile_zip(polygon_zip(2), max_vertices=9)


def test_shapefile_limits_are_enforced_before_any_shapely_geometry_is_built(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("a shapely geometry was built before the limit was checked")

    zipped = polygon_zip(2)
    monkeypatch.setattr(shapefile_module, "shape", boom)
    with pytest.raises(ParseError, match="vertices"):
        parse_shapefile_zip(zipped, max_vertices=4)


def test_zip_limits_can_still_be_passed_through_parse_shapefile_zip():
    with pytest.raises(ParseError, match="entries"):
        parse_shapefile_zip(point_zip(1), max_entries=1)
