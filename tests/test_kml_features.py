import pytest

from app.parsers.kml import parse_kml
from tests.test_kml_content import kml


def placemark(geometry: str, extra: str = "", attrs: str = "") -> str:
    return f"<Placemark{attrs}>{extra}{geometry}</Placemark>"


def one(body: str):
    parsed = parse_kml(kml(body))
    assert len(parsed.features) == 1
    return parsed.features[0], parsed


def test_point_drops_altitude_and_reads_name_description_and_id():
    feature, parsed = one(
        placemark(
            "<Point><coordinates>10.5,50.25,120</coordinates></Point>",
            "<name> Spot </name><description>a point</description>",
            ' id="p1"',
        )
    )
    assert feature.index == 0
    assert feature.source_id == "p1"
    assert feature.geometry.geom_type == "Point"
    assert (feature.geometry.x, feature.geometry.y) == (10.5, 50.25)
    assert not feature.geometry.has_z
    assert feature.properties == {"name": "Spot", "description": "a point"}
    assert parsed.warnings == []


def test_linestring_with_arbitrary_whitespace_between_tuples():
    feature, _ = one(
        placemark("<LineString><coordinates>\n  0,0,0\t1,0,0\n\n 2,1 </coordinates></LineString>")
    )
    assert feature.geometry.geom_type == "LineString"
    assert list(feature.geometry.coords) == [(0, 0), (1, 0), (2, 1)]


def test_spaces_around_commas_inside_a_tuple_are_tolerated():
    feature, _ = one(placemark("<Point><coordinates>10, 20</coordinates></Point>"))
    assert (feature.geometry.x, feature.geometry.y) == (10, 20)


RING = "0,0 1,0 1,1 0,1 0,0"
HOLE = "0.4,0.4 0.6,0.4 0.6,0.6 0.4,0.6 0.4,0.4"


def test_polygon_with_inner_boundary_keeps_its_hole():
    poly = (
        f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{RING}</coordinates></LinearRing>"
        f"</outerBoundaryIs><innerBoundaryIs><LinearRing><coordinates>{HOLE}</coordinates>"
        "</LinearRing></innerBoundaryIs></Polygon>"
    )
    feature, _ = one(placemark(poly))
    assert feature.geometry.geom_type == "Polygon"
    assert len(feature.geometry.interiors) == 1
    assert feature.geometry.area == pytest.approx(1 - 0.04)


def test_polygon_with_two_holes():
    hole2 = "0.7,0.7 0.8,0.7 0.8,0.8 0.7,0.8 0.7,0.7"
    poly = (
        f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{RING}</coordinates></LinearRing>"
        f"</outerBoundaryIs><innerBoundaryIs><LinearRing><coordinates>{HOLE}</coordinates>"
        f"</LinearRing></innerBoundaryIs><innerBoundaryIs><LinearRing><coordinates>{hole2}"
        "</coordinates></LinearRing></innerBoundaryIs></Polygon>"
    )
    feature, _ = one(placemark(poly))
    assert len(feature.geometry.interiors) == 2


def test_bare_linearring_becomes_a_linestring():
    feature, _ = one(placemark(f"<LinearRing><coordinates>{RING}</coordinates></LinearRing>"))
    assert feature.geometry.geom_type == "LineString"
    assert len(feature.geometry.coords) == 5


def test_multigeometry_of_polygons_becomes_a_multipolygon():
    def poly(ring):
        return (
            f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{ring}</coordinates>"
            "</LinearRing></outerBoundaryIs></Polygon>"
        )

    other = "5,5 6,5 6,6 5,6 5,5"
    feature, _ = one(placemark(f"<MultiGeometry>{poly(RING)}{poly(other)}</MultiGeometry>"))
    assert feature.geometry.geom_type == "MultiPolygon"
    assert len(feature.geometry.geoms) == 2


def test_multigeometry_with_mixed_types_becomes_a_geometry_collection():
    mg = (
        "<MultiGeometry><Point><coordinates>1,1</coordinates></Point>"
        "<LineString><coordinates>0,0 1,0</coordinates></LineString></MultiGeometry>"
    )
    feature, _ = one(placemark(mg))
    assert feature.geometry.geom_type == "GeometryCollection"
    assert {g.geom_type for g in feature.geometry.geoms} == {"Point", "LineString"}


def test_nested_multigeometry_is_flattened():
    mg = (
        "<MultiGeometry><MultiGeometry><Point><coordinates>1,1</coordinates></Point>"
        "<Point><coordinates>2,2</coordinates></Point></MultiGeometry>"
        "<Point><coordinates>3,3</coordinates></Point></MultiGeometry>"
    )
    feature, _ = one(placemark(mg))
    assert feature.geometry.geom_type == "MultiPoint"
    assert len(feature.geometry.geoms) == 3


def test_nested_folders_are_walked_in_document_order_with_sequential_indexes():
    pt = "<Point><coordinates>{0},0</coordinates></Point>"
    body = (
        f"<Folder><name>a</name>{placemark(pt.format(1), '<name>one</name>')}"
        f"<Folder>{placemark(pt.format(2), '<name>two</name>')}"
        f"<Folder>{placemark(pt.format(3), '<name>three</name>')}</Folder></Folder></Folder>"
        f"{placemark(pt.format(4), '<name>four</name>')}"
    )
    parsed = parse_kml(kml(body))
    assert [f.index for f in parsed.features] == [0, 1, 2, 3]
    assert [f.properties["name"] for f in parsed.features] == ["one", "two", "three", "four"]


def test_extended_data_and_schema_data_become_properties():
    extra = (
        "<name>n</name><ExtendedData>"
        '<Data name="owner"><value>Ada</value></Data>'
        '<Data name="floors"><value>3</value></Data>'
        '<SchemaData schemaUrl="#s"><SimpleData name="zone">R2</SimpleData></SchemaData>'
        "</ExtendedData>"
    )
    feature, _ = one(placemark("<Point><coordinates>1,1</coordinates></Point>", extra))
    assert feature.properties == {"name": "n", "owner": "Ada", "floors": "3", "zone": "R2"}


def test_extended_data_does_not_override_name():
    extra = (
        '<name>real</name><ExtendedData><Data name="name"><value>x</value></Data></ExtendedData>'
    )
    feature, _ = one(placemark("<Point><coordinates>1,1</coordinates></Point>", extra))
    assert feature.properties["name"] == "real"


def test_kml_2_1_namespace_is_accepted():
    data = (
        b'<kml xmlns="http://earth.google.com/kml/2.1"><Placemark><Point>'
        b"<coordinates>1,2</coordinates></Point></Placemark></kml>"
    )
    assert parse_kml(data).features[0].geometry.x == 1


def test_placemark_without_geometry_is_kept_with_a_warning():
    parsed = parse_kml(
        kml(
            placemark("", "<name>empty</name>")
            + placemark("<Point><coordinates>1,1</coordinates></Point>")
        )
    )
    assert [f.index for f in parsed.features] == [0, 1]
    assert parsed.features[0].geometry is None
    assert parsed.features[0].properties == {"name": "empty"}
    assert parsed.features[1].geometry is not None
    assert len(parsed.warnings) == 1 and "Placemark 0" in parsed.warnings[0]


@pytest.mark.parametrize(
    "geometry",
    [
        "<Point><coordinates>abc,def</coordinates></Point>",
        "<Point><coordinates>10</coordinates></Point>",
        "<Point><coordinates></coordinates></Point>",
        "<Point><coordinates>nan,1</coordinates></Point>",
        "<Point><coordinates>1,NaN</coordinates></Point>",
        "<Point><coordinates>inf,1</coordinates></Point>",
        "<Point><coordinates>1,-inf</coordinates></Point>",
        "<Point><coordinates>1e999,1</coordinates></Point>",
        "<Point><coordinates>1,1,nan</coordinates></Point>",
        "<LineString><coordinates>0,0 1,inf</coordinates></LineString>",
        "<MultiGeometry><Point><coordinates>0,0</coordinates></Point>"
        "<Point><coordinates>nan,0</coordinates></Point></MultiGeometry>",
        "<LineString><coordinates>0,0</coordinates></LineString>",
        "<Polygon><outerBoundaryIs><LinearRing><coordinates>0,0 1,1</coordinates>"
        "</LinearRing></outerBoundaryIs></Polygon>",
        "<Polygon></Polygon>",
        "<MultiGeometry></MultiGeometry>",
    ],
)
def test_malformed_geometry_keeps_the_feature_with_no_geometry_and_a_warning(geometry):
    ok = placemark("<Point><coordinates>1,1</coordinates></Point>")
    parsed = parse_kml(kml(placemark(geometry, "<name>bad</name>") + ok))
    assert [f.index for f in parsed.features] == [0, 1]
    assert parsed.features[0].geometry is None
    assert parsed.features[0].properties["name"] == "bad"
    assert parsed.features[1].geometry.x == 1
    assert len(parsed.warnings) == 1 and "Placemark 0" in parsed.warnings[0]


def test_unsupported_geometry_element_is_warned_not_crashed():
    track = '<gx:Track xmlns:gx="http://www.google.com/kml/ext/2.2"/>'
    parsed = parse_kml(kml(placemark(track)))
    assert parsed.features[0].geometry is None
    assert "unsupported" in parsed.warnings[0]
