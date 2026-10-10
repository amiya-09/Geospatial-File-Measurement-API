import tracemalloc

import pytest
import shapefile
from hypothesis import given, settings
from hypothesis import strategies as st

from app.config import (
    MAX_DBF_BYTES,
    MAX_FEATURES,
    MAX_PARTS,
    MAX_REPAIR_VERTICES,
    MAX_UNCOMPRESSED_BYTES,
    MAX_VERTICES,
    MAX_XML_ELEMENTS,
)
from app.parsers import kml as kml_module
from app.parsers import shapefile as shapefile_module
from app.parsers.base import Budget, ParseError
from app.parsers.kml import parse_kml
from app.parsers.shapefile import parse_shapefile_zip
from tests.fixtures import shapefile_parts, shapefile_zip
from tests.test_kml_content import kml


def test_default_limits():
    assert MAX_FEATURES == 10_000
    assert MAX_VERTICES == 1_000_000
    assert MAX_PARTS == 20_000
    assert MAX_REPAIR_VERTICES == 350
    assert MAX_UNCOMPRESSED_BYTES == 100 * 1024 * 1024
    assert MAX_XML_ELEMENTS == 500_000
    assert MAX_DBF_BYTES == 25 * 1024 * 1024


def test_budget_counts_cumulatively_and_allows_exactly_the_limit():
    budget = Budget(5, "vertices")
    budget.take(3)
    budget.take(2)
    with pytest.raises(ParseError, match="more than 5 vertices"):
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


def test_kml_vertex_cap_is_checked_before_the_coordinates_are_split():
    # Splitting a 25 MB <coordinates> text into 6.5 million tokens took 452 MB before the limit
    # could reject it. Here 0.8 MB of tokens took 13 MB at peak.
    data = kml(line_placemark(0).replace("</coordinates>", "1,1 " * 200_000 + "</coordinates>"))
    tracemalloc.start()
    try:
        with pytest.raises(ParseError, match="more than 10 vertices"):
            parse_kml(data, max_vertices=10)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 4 * len(data)


@settings(max_examples=500, deadline=None)
@given(st.text(alphabet="12,. \t\n\u00a0\u3000x", max_size=40), st.integers(0, 12))
def test_kml_tokens_are_counted_as_the_coordinate_parser_splits_them(text, stop_after):
    tokens = kml_module._COMMA_SPACES.sub(",", text).split()
    assert kml_module._count_tokens(text, stop_after) == min(len(tokens), stop_after + 1)


def test_kml_limits_are_enforced_before_any_shapely_geometry_is_built(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("a shapely geometry was built before the limit was checked")

    for name in ("Point", "LineString", "Polygon"):
        monkeypatch.setattr(kml_module, name, boom)
    with pytest.raises(ParseError, match="vertices"):
        parse_kml(kml(line_placemark(50)), max_vertices=10)


def multi_line_placemark(n_lines: int) -> str:
    lines = "".join(
        f"<LineString><coordinates>{i},0 {i},1</coordinates></LineString>" for i in range(n_lines)
    )
    return f"<Placemark><MultiGeometry>{lines}</MultiGeometry></Placemark>"


def test_kml_part_cap_counts_lines_and_polygon_rings_across_features():
    body = multi_line_placemark(2) + multi_line_placemark(1)
    assert len(parse_kml(kml(body), max_parts=3).features) == 2
    with pytest.raises(ParseError, match="more than 2 parts"):
        parse_kml(kml(body), max_parts=2)
    holed = (
        "<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>0,0 4,0 4,4 0,4 0,0"
        "</coordinates></LinearRing></outerBoundaryIs><innerBoundaryIs><LinearRing>"
        "<coordinates>1,1 2,1 2,2 1,2 1,1</coordinates></LinearRing></innerBoundaryIs>"
        "</Polygon></Placemark>"
    )
    assert parse_kml(kml(holed), max_parts=2).features[0].geometry.geom_type == "Polygon"
    with pytest.raises(ParseError, match="parts"):
        parse_kml(kml(holed), max_parts=1)


def test_kml_points_do_not_count_as_parts():
    points = "".join(f"<Point><coordinates>{i},0</coordinates></Point>" for i in range(5))
    body = f"<Placemark><MultiGeometry>{points}</MultiGeometry></Placemark>"
    assert len(parse_kml(kml(body), max_parts=1).features[0].geometry.geoms) == 5


def test_kml_part_cap_is_enforced_before_any_shapely_geometry_is_built(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("a shapely geometry was built before the limit was checked")

    for name in ("Point", "LineString", "Polygon"):
        monkeypatch.setattr(kml_module, name, boom)
    with pytest.raises(ParseError, match="parts"):
        parse_kml(kml(multi_line_placemark(4)), max_parts=3)


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


def multi_line_zip(parts_per_feature: list[int]) -> bytes:
    def build(w):
        for f, n in enumerate(parts_per_feature):
            w.line([[(i, f), (i, f + 0.5)] for i in range(n)])
            w.record(f"l{f}")

    return shapefile_zip(shapefile_parts(build, shapefile.POLYLINE), prj=None)


def test_shapefile_part_cap_is_cumulative_across_features():
    zipped = multi_line_zip([2, 2])
    assert len(parse_shapefile_zip(zipped, max_parts=4).features) == 2
    with pytest.raises(ParseError, match="more than 3 parts"):
        parse_shapefile_zip(zipped, max_parts=3)


def test_shapefile_points_do_not_count_as_parts():
    assert len(parse_shapefile_zip(point_zip(5), max_parts=1).features) == 5


def test_shapefile_part_cap_is_enforced_before_any_shapely_geometry_is_built(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("a shapely geometry was built before the limit was checked")

    zipped = multi_line_zip([4])
    monkeypatch.setattr(shapefile_module, "shape", boom)
    with pytest.raises(ParseError, match="parts"):
        parse_shapefile_zip(zipped, max_parts=3)


SIX_VERTICES = [(0, 0), (0, 1), (1, 1), (1, 0.5), (1, 0), (0, 0)]
SIX_VERTICES_3D = [(x, y, 5) for x, y in SIX_VERTICES]
ONE_SHAPE_OF_EACH_TYPE = {
    "line": (shapefile.POLYLINE, lambda w: w.line([SIX_VERTICES])),
    "linez": (shapefile.POLYLINEZ, lambda w: w.linez([SIX_VERTICES_3D])),
    "linem": (shapefile.POLYLINEM, lambda w: w.linem([SIX_VERTICES_3D])),
    "polygon": (shapefile.POLYGON, lambda w: w.poly([SIX_VERTICES])),
    "polygonz": (shapefile.POLYGONZ, lambda w: w.polyz([SIX_VERTICES_3D])),
    "multipoint": (shapefile.MULTIPOINT, lambda w: w.multipoint(SIX_VERTICES)),
    "multipointz": (shapefile.MULTIPOINTZ, lambda w: w.multipointz(SIX_VERTICES_3D)),
    "multipatch": (
        shapefile.MULTIPATCH,
        lambda w: w.multipatch([SIX_VERTICES_3D], partTypes=[shapefile.OUTER_RING]),
    ),
}


@pytest.mark.parametrize("kind", sorted(ONE_SHAPE_OF_EACH_TYPE))
def test_shapefile_vertex_cap_is_checked_from_the_record_header_before_pyshp_reads_the_shape(
    kind, monkeypatch
):
    # pyshp builds all points of a shape at once: a 233 KB ZIP holding one 6,000,000 point
    # line used 1.1 GB before the limit could reject it. Z and M values are not vertices.
    shape_type, write = ONE_SHAPE_OF_EACH_TYPE[kind]
    parts = shapefile_parts(lambda w: (write(w), w.record(kind)), shape_type)
    zipped = shapefile_zip(parts, prj=None)
    assert len(parse_shapefile_zip(zipped, max_vertices=6).features) == 1

    def boom(*args, **kwargs):
        raise AssertionError("pyshp read the shape before the vertex limit was checked")

    monkeypatch.setattr(shapefile.Reader, "shape", boom)
    with pytest.raises(ParseError, match="more than 5 vertices"):
        parse_shapefile_zip(zipped, max_vertices=5)


def test_shapefile_part_cap_is_checked_from_the_record_header_before_pyshp_reads_the_shape(
    monkeypatch,
):
    zipped = multi_line_zip([4])

    def boom(*args, **kwargs):
        raise AssertionError("pyshp read the shape before the part limit was checked")

    monkeypatch.setattr(shapefile.Reader, "shape", boom)
    with pytest.raises(ParseError, match="more than 3 parts"):
        parse_shapefile_zip(zipped, max_parts=3)


def test_zip_limits_can_still_be_passed_through_parse_shapefile_zip():
    with pytest.raises(ParseError, match="entries"):
        parse_shapefile_zip(point_zip(1), max_entries=1)
