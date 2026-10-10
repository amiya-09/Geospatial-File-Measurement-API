"""Pin why a 10 degree box is about 0.378% below Geod.

The gap is an edge interpretation difference, not projection distortion. Geod joins the
corners with geodesics. The projected polygon joins them with straight lines in the local
map. Cut the edges every 0.1 degrees so both sides follow the meridians and parallels, and
the gap shrinks from -0.378% to under 0.00004%. If the projection were distorting the
area, cutting the edges would not make the gap vanish.

Numbers measured on 2026-10-10 for box(0, 0, 10, 10).
"""

import pytest
import shapely
from pyproj import Geod
from shapely.geometry import box

from app.geo.measure import measure

GEOD = Geod(ellps="WGS84")
BOX = box(0, 0, 10, 10)


def geod_area(poly):
    return abs(GEOD.geometry_area_perimeter(poly)[0])


def test_corner_edges_gap_is_about_minus_0_378_percent():
    gap = measure(BOX).area_m2 / geod_area(BOX) - 1
    assert gap == pytest.approx(-0.003779, abs=2e-6)


@pytest.mark.parametrize(
    ("step", "max_abs_gap"),
    [(5, 1.0e-3), (1, 4.0e-5), (0.5, 1.0e-5), (0.1, 1.0e-6)],
)
def test_gap_shrinks_as_edges_follow_lon_lat_lines(step, max_abs_gap):
    cut = shapely.segmentize(BOX, step)
    gap = measure(cut).area_m2 / geod_area(cut) - 1
    assert abs(gap) < max_abs_gap
