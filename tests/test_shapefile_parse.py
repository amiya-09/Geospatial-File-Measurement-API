import datetime

import pytest
import shapefile
from pyproj import CRS

from app.parsers.base import ParseError, UnmeasurableError
from app.parsers.shapefile import parse_shapefile_zip
from tests.fixtures import make_zip, shapefile_parts, shapefile_zip

WGS84_PRJ = CRS.from_epsg(4326).to_wkt()
WGS84_ESRI_PRJ = CRS.from_epsg(4326).to_wkt(version="WKT1_ESRI")


def parse(parts, prj=WGS84_PRJ, **kwargs):
    return parse_shapefile_zip(shapefile_zip(parts, prj=prj, **kwargs))


def points(coords, fields=(("name", "C", 40, 0),), records=None, **kwargs):
    def build(w):
        for i, (x, y) in enumerate(coords):
            w.point(x, y)
            w.record(*(records[i] if records else [f"p{i}"]))

    return shapefile_parts(build, shapefile.POINT, fields=fields, **kwargs)


def test_points_with_attributes():
    parsed = parse(points([(10, 50), (11, 51)]))
    assert [f.index for f in parsed.features] == [0, 1]
    assert [f.properties for f in parsed.features] == [{"name": "p0"}, {"name": "p1"}]
    assert parsed.features[0].source_id is None
    assert (parsed.features[1].geometry.x, parsed.features[1].geometry.y) == (11, 51)
    assert parsed.crs.to_epsg() == 4326
    assert parsed.warnings == []


def test_attribute_types_become_json_friendly_values():
    fields = (("n", "N", 10, 0), ("f", "N", 12, 3), ("ok", "L", 1, 0), ("day", "D", 8, 0))
    parts = points([(1, 1)], fields=fields, records=[[7, 2.5, True, datetime.date(2024, 5, 17)]])
    props = parse(parts).features[0].properties
    assert props == {"n": 7, "f": 2.5, "ok": True, "day": "2024-05-17"}


def test_polygon_with_hole_and_multipart_polygon():
    def build(w):
        # shapefile rule: outer rings clockwise, holes counter-clockwise
        w.poly(
            [
                [(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)],
                [(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6), (0.4, 0.4)],
            ]
        )
        w.record("holey")
        w.poly([[(5, 5), (5, 6), (6, 6), (6, 5), (5, 5)], [(8, 8), (8, 9), (9, 9), (9, 8), (8, 8)]])
        w.record("two parts")

    parsed = parse(shapefile_parts(build, shapefile.POLYGON))
    holey, multi = (f.geometry for f in parsed.features)
    assert holey.geom_type == "Polygon" and len(holey.interiors) == 1
    assert holey.area == pytest.approx(0.96)
    assert multi.geom_type == "MultiPolygon" and len(multi.geoms) == 2


def test_polylines_single_and_multipart():
    def build(w):
        w.line([[(0, 0), (1, 0), (1, 1)]])
        w.record("one")
        w.line([[(0, 0), (1, 0)], [(5, 5), (6, 5)]])
        w.record("two")

    parsed = parse(shapefile_parts(build, shapefile.POLYLINE))
    assert [f.geometry.geom_type for f in parsed.features] == ["LineString", "MultiLineString"]


def test_multipoint():
    parts = shapefile_parts(
        lambda w: (w.multipoint([(0, 0), (1, 1)]), w.record("m")), shapefile.MULTIPOINT
    )
    assert parse(parts).features[0].geometry.geom_type == "MultiPoint"


def test_z_shapes_are_readable():
    parts = shapefile_parts(lambda w: (w.pointz(3, 4, 99), w.record("z")), shapefile.POINTZ)
    geom = parse(parts).features[0].geometry
    assert (geom.x, geom.y) == (3, 4)


def test_null_shape_keeps_its_index_with_no_geometry_and_a_warning():
    def build(w):
        w.point(1, 1)
        w.record("a")
        w.null()
        w.record("b")
        w.point(2, 2)
        w.record("c")

    parsed = parse(shapefile_parts(build, shapefile.POINT))
    assert [f.index for f in parsed.features] == [0, 1, 2]
    assert parsed.features[1].geometry is None
    assert parsed.features[1].properties == {"name": "b"}
    assert parsed.features[2].geometry.x == 2
    assert len(parsed.warnings) == 1 and "feature 1" in parsed.warnings[0]


def test_unrepresentable_shape_type_keeps_the_feature_with_a_warning():
    parts = shapefile_parts(
        lambda w: (
            w.multipatch(
                [[(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]], partTypes=[shapefile.TRIANGLE_STRIP]
            ),
            w.record("mp"),
        ),
        shapefile.MULTIPATCH,
    )
    parsed = parse(parts)
    assert parsed.features[0].geometry is None
    assert "feature 0" in parsed.warnings[0]


def test_shapefile_with_no_records_has_no_features():
    parsed = parse(shapefile_parts(lambda w: None, shapefile.POINT))
    assert parsed.features == []


# ---- attribute encoding ----


def test_utf8_attributes_with_cpg():
    parts = points([(1, 1)], records=[["café ☕"]])
    assert parse(parts, cpg="UTF-8").features[0].properties["name"] == "café ☕"


def test_latin1_attributes_declared_by_cpg():
    parts = points([(1, 1)], records=[["café"]], encoding="latin-1")
    parsed = parse(parts, cpg="ISO-8859-1")
    assert parsed.features[0].properties["name"] == "café"
    assert parsed.warnings == []


def test_cpg_with_a_windows_code_page_number():
    parts = points([(1, 1)], records=[["café"]], encoding="cp1252")
    assert parse(parts, cpg="1252").features[0].properties["name"] == "café"


def test_latin1_attributes_without_cpg_fall_back_with_a_warning():
    parts = points([(1, 1)], records=[["café"]], encoding="latin-1")
    parsed = parse(parts)
    assert parsed.features[0].properties["name"] == "café"
    assert any("latin-1" in w for w in parsed.warnings)


def test_unknown_cpg_is_ignored_with_a_warning():
    parts = points([(1, 1)], records=[["plain"]])
    parsed = parse(parts, cpg="NOT-A-CODEC")
    assert parsed.features[0].properties["name"] == "plain"
    assert any("NOT-A-CODEC" in w for w in parsed.warnings)


# ---- CRS ----


def test_esri_flavoured_prj_is_understood():
    assert parse(points([(1, 1)]), prj=WGS84_ESRI_PRJ).crs.to_epsg() == 4326


def test_projected_prj_is_used_as_is_and_coordinates_stay_untransformed():
    utm = CRS.from_epsg(32632)
    parsed = parse(points([(500000, 4649776)]), prj=utm.to_wkt())
    assert parsed.crs.to_epsg() == 32632
    assert parsed.features[0].geometry.x == 500000
    assert parsed.warnings == []


def test_missing_prj_with_lon_lat_coordinates_assumes_wgs84_with_a_warning():
    parsed = parse(points([(10, 50), (-120, -45)]), prj=None)
    assert parsed.crs.to_epsg() == 4326
    assert len(parsed.warnings) == 1
    assert ".prj" in parsed.warnings[0] and "4326" in parsed.warnings[0]


def test_missing_prj_with_projected_coordinates_is_unmeasurable():
    with pytest.raises(UnmeasurableError, match=r"\.prj"):
        parse(points([(500000, 4649776)]), prj=None)


def test_missing_prj_with_no_coordinates_assumes_wgs84_with_a_warning():
    parsed = parse(shapefile_parts(lambda w: None, shapefile.POINT), prj=None)
    assert parsed.crs.to_epsg() == 4326 and parsed.warnings


@pytest.mark.parametrize("prj", ["", "not wkt at all", "GEOGCS["])
def test_unreadable_prj_is_unmeasurable(prj):
    with pytest.raises(UnmeasurableError, match=r"\.prj"):
        parse(points([(1, 1)]), prj=prj)


# ---- corrupt content ----


def test_garbage_shapefile_members_in_a_valid_zip_are_rejected():
    zipped = make_zip(
        {"data.shp": b"nope" * 50, "data.shx": b"nope" * 20, "data.dbf": b"nope" * 20}
    )
    with pytest.raises(ParseError):
        parse_shapefile_zip(zipped)


def test_truncated_shp_is_rejected():
    parts = points([(1, 1), (2, 2)])
    parts["shp"] = parts["shp"][:110]
    with pytest.raises(ParseError):
        parse(parts)
