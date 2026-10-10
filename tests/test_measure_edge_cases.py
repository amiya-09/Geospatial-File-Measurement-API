import pytest
from pyproj import Geod
from shapely.geometry import GeometryCollection, LinearRing, LineString, Point, Polygon, box

from app.geo.measure import MeasurementError, measure
from tests import expected_values as ev

GEOD = Geod(ellps="WGS84")


def geod_area(coords):
    lons, lats = zip(*coords, strict=True)
    return abs(GEOD.polygon_area_perimeter(lons, lats)[0])


def test_valid_polygon_reports_valid_with_no_reason():
    m = measure(Polygon(ev.BOX_EQUATOR["coords"]))
    assert m.is_valid is True
    assert m.validity_reason is None


def test_bowtie_is_flagged_and_measured_as_the_sum_of_its_two_triangles():
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
    m = measure(bowtie)
    assert m.is_valid is False
    assert m.validity_reason.startswith("Self-intersection")
    left = [(0, 0), (0.5, 0.5), (0, 1)]
    right = [(0.5, 0.5), (1, 1), (1, 0)]
    assert m.area_m2 == pytest.approx(geod_area(left) + geod_area(right), rel=0.001)


def test_repair_that_yields_a_geometry_collection_measures_polygon_parts_only():
    # make_valid turns this ring-with-a-spike into Polygon + LineString (GeometryCollection).
    spiked = Polygon([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0), (-1, 0), (0, 0)])
    m = measure(spiked)
    assert m.is_valid is False
    square = [(0, 0), (2, 0), (2, 2), (0, 2)]
    assert m.area_m2 == pytest.approx(geod_area(square), rel=0.001)
    assert m.length_m is None  # the repaired spike line must not count as a length


def test_polygon_that_collapses_to_a_line_has_no_area():
    collapsed = Polygon([(0, 0), (1, 0), (2, 0), (0, 0)])
    m = measure(collapsed)
    assert m.is_valid is False
    assert m.validity_reason is not None
    assert m.area_m2 is None and m.length_m is None
    assert m.reason == "no polygonal area after repair"


def test_z_values_do_not_change_results():
    flat = [(0, 0), (1, 0), (1, 1), (0, 1)]
    raised = [(x, y, 5000.0 * (i + 1)) for i, (x, y) in enumerate(flat)]
    assert measure(Polygon(raised)).area_m2 == measure(Polygon(flat)).area_m2
    line_flat = LineString([(0, 0), (1, 0)])
    line_z = LineString([(0, 0, 0), (1, 0, 900000)])
    assert measure(line_z).length_m == measure(line_flat).length_m


def test_empty_polygon_is_reported_not_raised():
    m = measure(Polygon())
    assert m.area_m2 is None and m.length_m is None
    assert m.reason == "empty geometry"
    assert m.is_valid is True


@pytest.mark.parametrize("empty", [Polygon(), LineString()], ids=["empty-polygon", "empty-line"])
def test_an_empty_part_inside_a_collection_is_skipped(empty):
    # An empty part has NaN bounds; it used to reach the projection and crash measure().
    m = measure(GeometryCollection([empty, box(0, 0, 1, 1), LineString([(0, 0), (1, 0)])]))
    assert m.area_m2 == pytest.approx(geod_area([(0, 0), (1, 0), (1, 1), (0, 1)]), rel=0.001)
    assert m.length_m == pytest.approx(measure(LineString([(0, 0), (1, 0)])).length_m)
    assert m.part_count == 2


def test_unsupported_geometry_type_is_reported_not_raised():
    m = measure(LinearRing([(0, 0), (1, 0), (1, 1)]))
    assert m.area_m2 is None and m.length_m is None
    assert m.reason == "unsupported"


@pytest.mark.parametrize(
    "geom",
    [
        Polygon([(500000, 4000000), (500100, 4000000), (500100, 4000100)]),  # projected metres
        Point(0, 91),
        LineString([(-181, 0), (0, 0)]),
    ],
)
def test_coordinates_outside_lon_lat_range_raise_instead_of_returning_garbage(geom):
    with pytest.raises(MeasurementError):
        measure(geom)


@pytest.mark.filterwarnings("ignore:invalid value encountered:RuntimeWarning")
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_coordinates_raise_instead_of_passing_the_range_guard(bad):
    # NaN compares False against every bound, so it must be rejected explicitly.
    with pytest.raises(MeasurementError):
        measure(Polygon([(0, 0), (1, 0), (1, bad), (0, 1)]))
    with pytest.raises(MeasurementError):
        measure(Point(bad, 1))
