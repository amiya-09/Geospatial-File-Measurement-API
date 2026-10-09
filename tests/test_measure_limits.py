"""Documented accuracy limits and known behaviours of the local-projection approach.

These tests pin observed behaviour so it is explicit, not accidental. Observed values
come from runs against pyproj.Geod(ellps="WGS84").
"""

import pytest
from pyproj import Geod
from shapely.geometry import LineString, Polygon

from app.geo.measure import MeasurementError, measure

GEOD = Geod(ellps="WGS84")


def geod_length(coords):
    lons, lats = zip(*coords, strict=True)
    return GEOD.line_length(lons, lats)


def geod_area(coords):
    lons, lats = zip(*coords, strict=True)
    return abs(GEOD.polygon_area_perimeter(lons, lats)[0])


def test_long_line_far_from_its_centroid_exceeds_the_0_1_percent_target_but_stays_small():
    # Two 20 degree legs (~2,200 km each). The projection is centred on the line's centroid,
    # and AEQD is only exact along lines through its centre, so the legs are slightly off.
    # Observed: +0.126% (projected longer than geodesic). Shorter lines are far better,
    # e.g. a 5 degree L is +0.008%.
    coords = [(0, 0), (20, 0), (20, 20)]
    rel_error = measure(LineString(coords)).length_m / geod_length(coords) - 1
    assert 0.001 < rel_error < 0.002


def test_large_polygon_is_underestimated_because_projected_edges_are_not_geodesics():
    # 10 degree box (~1.2 million km2). Observed: -0.378%. 1 degree box: -0.004%.
    coords = [(0, 0), (10, 0), (10, 10), (0, 10)]
    rel_error = measure(Polygon(coords)).area_m2 / geod_area(coords) - 1
    assert -0.005 < rel_error < -0.003


def test_antimeridian_feature_with_wrapped_longitudes_is_currently_wildly_wrong():
    # KNOWN LIMITATION, not desired behaviour. A 2 degree wide box straddling 180 with
    # wrapped longitudes (179 -> -179) is read as a 358 degree wide box, so the result is
    # ~3,700x too large (area) with no warning and is_valid=True. See the stage A report.
    coords = [(179, 0), (179, 1), (-179, 1), (-179, 0)]
    true_area = geod_area([(179, 0), (179, 1), (181, 1), (181, 0)])
    m = measure(Polygon(coords))
    assert m.is_valid is True
    assert m.area_m2 > 1000 * true_area


def test_antimeridian_feature_with_continuous_longitudes_is_rejected():
    # Longitudes 179..181 are outside [-180, 180], so the lon/lat guard raises.
    with pytest.raises(MeasurementError):
        measure(Polygon([(179, 0), (179, 1), (181, 1), (181, 0)]))
