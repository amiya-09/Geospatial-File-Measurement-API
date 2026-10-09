import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)

from app.geo.crs import to_wgs84
from app.geo.measure import measure
from tests import expected_values as ev

GEOD = Geod(ellps="WGS84")


def geod_area(coords):
    lons, lats = zip(*coords, strict=True)
    return abs(GEOD.polygon_area_perimeter(lons, lats)[0])


def geod_length(coords):
    lons, lats = zip(*coords, strict=True)
    return GEOD.line_length(lons, lats)


def test_box_at_equator_matches_reference():
    m = measure(Polygon(ev.BOX_EQUATOR["coords"]))
    assert m.area_m2 == pytest.approx(ev.BOX_EQUATOR["area_m2"], rel=ev.BOX_EQUATOR["rel_tol"])
    assert m.length_m is None
    assert m.reason is None


def test_box_at_60n_matches_reference():
    m = measure(Polygon(ev.BOX_60N["coords"]))
    assert m.area_m2 == pytest.approx(ev.BOX_60N["area_m2"], rel=ev.BOX_60N["rel_tol"])


def test_polygon_with_hole_matches_reference():
    m = measure(Polygon(ev.POLYGON_WITH_HOLE["exterior"], [ev.POLYGON_WITH_HOLE["hole"]]))
    assert m.area_m2 == pytest.approx(
        ev.POLYGON_WITH_HOLE["area_m2"], rel=ev.POLYGON_WITH_HOLE["rel_tol"]
    )


def test_one_degree_of_longitude_line_matches_reference():
    m = measure(LineString(ev.LINE_EQUATOR_1DEG["coords"]))
    assert m.length_m == pytest.approx(
        ev.LINE_EQUATOR_1DEG["length_m"], rel=ev.LINE_EQUATOR_1DEG["rel_tol"]
    )
    assert m.area_m2 is None


@pytest.mark.parametrize(
    "south_west",
    [(-120.0, -45.0), (150.0, 70.0), (-70.0, -10.0), (0.0, 0.0)],
)
def test_area_matches_geodesic_in_other_hemispheres(south_west):
    lon, lat = south_west
    coords = [(lon, lat), (lon + 0.5, lat), (lon + 0.5, lat + 0.5), (lon, lat + 0.5)]
    assert measure(Polygon(coords)).area_m2 == pytest.approx(geod_area(coords), rel=0.001)


def test_regional_line_matches_geodesic():
    # ~130 km diagonal at mid latitude
    coords = [(10.0, 45.0), (10.5, 45.4), (11.0, 46.0)]
    assert measure(LineString(coords)).length_m == pytest.approx(geod_length(coords), rel=0.001)


def test_polygon_from_web_mercator_source_matches_geodesic_not_mercator_area():
    coords = ev.BOX_60N["coords"]
    fwd = Transformer.from_crs(4326, 3857, always_xy=True)
    merc = Polygon([fwd.transform(x, y) for x, y in coords])
    m = measure(to_wgs84(merc, CRS.from_epsg(3857)))
    assert m.area_m2 == pytest.approx(ev.BOX_60N["area_m2"], rel=0.001)
    # Naive Mercator area at 60N is inflated ~4x (1/cos^2); make sure we are nowhere near it.
    assert merc.area > 3 * ev.BOX_60N["area_m2"]


def test_multipolygon_sums_parts():
    a = ev.BOX_EQUATOR["coords"]
    b = [(x + 5, y) for x, y in a]
    m = measure(MultiPolygon([Polygon(a), Polygon(b)]))
    assert m.area_m2 == pytest.approx(geod_area(a) + geod_area(b), rel=0.001)


def test_multilinestring_sums_parts():
    a = [(0.0, 0.0), (1.0, 0.0)]
    b = [(0.0, 1.0), (0.0, 2.0)]
    m = measure(MultiLineString([a, b]))
    assert m.length_m == pytest.approx(geod_length(a) + geod_length(b), rel=0.001)


def test_point_has_no_measurement_and_a_reason():
    m = measure(Point(10, 50))
    assert m.area_m2 is None and m.length_m is None
    assert m.reason == "not applicable"
    assert m.is_valid is True


def test_multipoint_has_no_measurement_and_a_reason():
    m = measure(MultiPoint([(0, 0), (1, 1)]))
    assert m.area_m2 is None and m.length_m is None
    assert m.reason == "not applicable"


def test_geometry_collection_measures_each_part_by_type():
    poly = ev.BOX_EQUATOR["coords"]
    line = [(5.0, 0.0), (6.0, 0.0)]
    m = measure(GeometryCollection([Polygon(poly), LineString(line), Point(9, 9)]))
    assert m.area_m2 == pytest.approx(geod_area(poly), rel=0.001)
    assert m.length_m == pytest.approx(geod_length(line), rel=0.001)
    assert m.reason is None


def test_area_result_reports_unit_and_measurement_crs():
    m = measure(Polygon(ev.BOX_60N["coords"]))
    assert m.area_unit == "m2"
    assert "+proj=laea" in m.area_crs and "+datum=WGS84" in m.area_crs
    assert m.length_unit is None and m.length_crs is None


def test_length_result_reports_unit_and_measurement_crs():
    m = measure(LineString(ev.LINE_EQUATOR_1DEG["coords"]))
    assert m.length_unit == "m"
    assert "+proj=aeqd" in m.length_crs and "+datum=WGS84" in m.length_crs
    assert m.area_unit is None and m.area_crs is None


def test_unmeasured_feature_reports_no_unit_or_crs():
    m = measure(Point(1, 1))
    assert (m.area_unit, m.area_crs, m.length_unit, m.length_crs) == (None, None, None, None)


def test_distant_polygon_parts_equal_the_sum_of_independently_measured_parts():
    a = Polygon(ev.BOX_EQUATOR["coords"])
    b_coords = [(100, 50), (101, 50), (101, 51), (100, 51)]
    b = Polygon(b_coords)
    m = measure(MultiPolygon([a, b]))
    assert m.area_m2 == pytest.approx(measure(a).area_m2 + measure(b).area_m2, rel=1e-12)
    assert m.area_m2 == pytest.approx(
        geod_area(ev.BOX_EQUATOR["coords"]) + geod_area(b_coords), rel=0.001
    )
    assert "laea" in m.area_crs and "2" in m.area_crs  # one local CRS per part


def test_distant_line_parts_equal_the_sum_of_independently_measured_parts():
    a = LineString([(0, 0), (1, 0)])
    b = LineString([(-120, -45), (-119, -45)])
    m = measure(MultiLineString([a, b]))
    assert m.length_m == pytest.approx(measure(a).length_m + measure(b).length_m, rel=1e-12)
    assert m.length_m == pytest.approx(
        geod_length(list(a.coords)) + geod_length(list(b.coords)), rel=0.001
    )


def test_single_part_keeps_its_exact_proj_string_as_crs():
    m = measure(MultiPolygon([Polygon(ev.BOX_EQUATOR["coords"])]))
    assert m.area_crs.startswith("+proj=laea +lat_0=")
