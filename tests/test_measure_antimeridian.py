import pytest
from pyproj import Geod
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon

from app.geo.measure import MeasurementError, measure

GEOD = Geod(ellps="WGS84")


def geod_area(coords):
    lons, lats = zip(*coords, strict=True)
    return abs(GEOD.polygon_area_perimeter(lons, lats)[0])


def geod_length(coords):
    lons, lats = zip(*coords, strict=True)
    return GEOD.line_length(lons, lats)


# The same 2 degree box straddling 180, written with continuous longitudes (the truth)
# and with wrapped longitudes (what a KML/shapefile usually contains).
UNWRAPPED_BOX = [(179, 0), (179, 1), (181, 1), (181, 0)]
WRAPPED_BOX = [(179, 0), (179, 1), (-179, 1), (-179, 0)]


def test_wrapped_box_matches_geodesic_area_of_the_unwrapped_box():
    m = measure(Polygon(WRAPPED_BOX))
    assert m.area_m2 == pytest.approx(geod_area(UNWRAPPED_BOX), rel=0.001)
    assert m.is_valid is True
    assert any("antimeridian" in w for w in m.warnings)


def test_wrapped_equator_line_is_about_222_km():
    m = measure(LineString([(179, 0), (-179, 0)]))
    assert m.length_m == pytest.approx(geod_length([(179, 0), (181, 0)]), rel=0.001)
    assert m.length_m == pytest.approx(222_639, rel=0.001)
    assert any("antimeridian" in w for w in m.warnings)


def test_features_that_do_not_cross_report_no_warnings():
    m = measure(Polygon([(10, 10), (11, 10), (11, 11), (10, 11)]))
    assert m.warnings == ()


def test_multipolygon_pre_split_at_180_sums_to_the_whole_box():
    east = [(179, 0), (179, 1), (180, 1), (180, 0)]
    west = [(-180, 0), (-180, 1), (-179, 1), (-179, 0)]
    m = measure(MultiPolygon([Polygon(east), Polygon(west)]))
    assert m.area_m2 == pytest.approx(geod_area(UNWRAPPED_BOX), rel=0.001)
    assert m.warnings == ()  # each part is narrow, so nothing needed unwrapping


def test_multilinestring_pre_split_at_180_sums_to_the_whole_line():
    m = measure(MultiLineString([[(179, 0), (180, 0)], [(-180, 0), (-179, 0)]]))
    assert m.length_m == pytest.approx(geod_length([(179, 0), (181, 0)]), rel=0.001)


def test_wrapped_part_is_unwrapped_independently_of_other_parts():
    near = [(10, 10), (11, 10), (11, 11), (10, 11)]
    multi = MultiPolygon([Polygon(WRAPPED_BOX), Polygon(near)])
    m = measure(multi)
    assert m.area_m2 == pytest.approx(geod_area(UNWRAPPED_BOX) + geod_area(near), rel=0.001)
    assert len(m.warnings) == 1


@pytest.mark.parametrize(
    "geom",
    [
        # Spans 340 degrees as written and still 340 with negatives shifted by +360.
        Polygon([(-170, 0), (10, 0), (170, 10), (-10, 10)]),
        LineString([(-170, 0), (-10, 0), (10, 0), (170, 0)]),
    ],
)
def test_feature_that_cannot_be_unwrapped_is_rejected(geom):
    with pytest.raises(MeasurementError, match="longitude"):
        measure(geom)


def test_continuous_longitudes_outside_range_are_still_rejected_by_the_range_guard():
    with pytest.raises(MeasurementError):
        measure(Polygon(UNWRAPPED_BOX))


# Unwrapping must never turn a non-empty part into a zero-measure one. -180 and 180 are the
# same meridian, so the +360 shift collapses a feature that spans the whole circle.
@pytest.mark.parametrize(
    "geom",
    [
        Polygon([(-180, -90), (180, -90), (180, 90), (-180, 90)]),
        Polygon([(-180, 10), (180, 10), (180, 20), (-180, 20)]),
        LineString([(-180, 0), (180, 0)]),
    ],
    ids=["whole_world_box", "mid_latitude_band", "full_circle_line"],
)
def test_feature_that_collapses_when_unwrapped_is_rejected(geom):
    with pytest.raises(MeasurementError, match="more than 180 degrees"):
        measure(geom)


def test_multipolygon_with_a_collapsing_part_is_rejected_not_dropped():
    normal = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    collapsing = Polygon([(-180, 10), (180, 10), (180, 20), (-180, 20)])
    with pytest.raises(MeasurementError, match="more than 180 degrees"):
        measure(MultiPolygon([normal, collapsing]))
