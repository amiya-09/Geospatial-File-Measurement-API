"""Property tests: invariants that must hold for any small polygon or line, not just the
hand-picked ones."""

import math

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString, Polygon

from app.geo.measure import measure

SETTINGS = settings(max_examples=200, deadline=None)

lons = st.floats(-170, 170)
lats = st.floats(-80, 80)
radii = st.floats(0.001, 1.0)


@st.composite
def star_polygons(draw):
    """A simple polygon: vertices at increasing angles around a centre, each at its own radius."""
    lon, lat, r = draw(lons), draw(lats), draw(radii)
    n = draw(st.integers(3, 20))
    angles = sorted(draw(st.lists(st.floats(0, 2 * math.pi), min_size=n, max_size=n, unique=True)))
    scales = draw(st.lists(st.floats(0.3, 1.0), min_size=n, max_size=n))
    ring = [
        (lon + r * s * math.cos(a), lat + r * s * math.sin(a))
        for a, s in zip(angles, scales, strict=True)
    ]
    poly = Polygon(ring)
    assume(poly.is_valid and poly.area > 1e-12)
    return ring


@SETTINGS
@given(star_polygons(), st.integers(0, 19), st.booleans())
def test_area_ignores_start_vertex_and_ring_orientation(ring, start, reverse):
    reference = measure(Polygon(ring)).area_m2
    start %= len(ring)
    rotated = ring[start:] + ring[:start]
    if reverse:
        rotated.reverse()
    assert measure(Polygon(rotated)).area_m2 == pytest.approx(reference, rel=1e-9)


@SETTINGS
@given(star_polygons(), st.integers(-9, 9))
def test_shifting_a_small_polygon_by_whole_degrees_of_longitude_keeps_its_area(ring, shift):
    # The ellipsoid is symmetric about its axis, so moving east or west must not change area.
    reference = measure(Polygon(ring)).area_m2
    shifted = [(x + shift, y) for x, y in ring]
    assert measure(Polygon(shifted)).area_m2 == pytest.approx(reference, rel=0.005)


@SETTINGS
@given(
    lons,
    lats,
    st.lists(st.tuples(st.floats(-0.5, 0.5), st.floats(-0.5, 0.5)), min_size=2, max_size=15),
)
def test_line_length_equals_the_sum_of_its_segments(lon, lat, offsets):
    points = [(lon + dx, lat + dy) for dx, dy in offsets]
    segments = list(zip(points, points[1:], strict=False))
    assume(all(a != b for a, b in segments))
    whole = measure(LineString(points)).length_m
    parts = sum(measure(LineString(seg)).length_m for seg in segments)
    assert whole == pytest.approx(parts, rel=1e-4)
