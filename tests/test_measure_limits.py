"""Documented accuracy limits and known behaviours of the local-projection approach.

These tests pin observed behaviour so it is explicit, not accidental. Observed values
come from runs against pyproj.Geod(ellps="WGS84").
"""

import pytest
from pyproj import Geod
import shapely
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


def test_large_polygon_gap_is_edge_interpretation_not_projection_distortion():
    # Densify the 10 degree box in lon/lat (0.2 degree steps), so its edges follow parallels
    # and meridians instead of a single straight segment.
    box = [(0, 0), (10, 0), (10, 10), (0, 10)]
    densified = shapely.segmentize(Polygon(box), 0.2)
    ring = list(densified.exterior.coords)[:-1]
    projected = measure(densified).area_m2

    # Observed: the -0.378% moves to -0.248% against the 4-vertex geodesic reference...
    assert -0.0027 < projected / geod_area(box) - 1 < -0.0023
    # ...but against Geod on the SAME densified ring the projection is off by only -0.00015%
    # (-0.004% at 1 degree steps, -0.00001% at 0.05). The equal-area projection itself is
    # not the problem; "which edges does the polygon have" is: geodesic (reference),
    # parallel-following (densified) and projected-straight (4 vertices) are three
    # different shapes, worth -0.378%, -0.248% and 0 respectively.
    assert projected == pytest.approx(geod_area(ring), rel=1e-5)


def test_antimeridian_feature_with_continuous_longitudes_is_rejected():
    # Longitudes 179..181 are outside [-180, 180], so the lon/lat guard raises.
    with pytest.raises(MeasurementError):
        measure(Polygon([(179, 0), (179, 1), (181, 1), (181, 0)]))
