"""Repair happens after unwrapping, and the polygons of a feature are repaired together.

References come from Geod with the edges cut every 0.01 degrees, so they follow the meridians
and parallels, and with rings oriented the way Geod expects (holes clockwise).
"""

import pytest
import shapely
from pyproj import Geod
from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon, box
from shapely.geometry.polygon import orient

from app.geo.measure import measure

GEOD = Geod(ellps="WGS84")


def geod_area(geom) -> float:
    """Geodesic area of a polygon or multipolygon given in unwrapped lon/lat."""
    polys = getattr(geom, "geoms", [geom])
    return sum(
        GEOD.geometry_area_perimeter(orient(shapely.segmentize(p, 0.01), 1.0))[0] for p in polys
    )


# A 2 degree box across 180 written with wrapped longitudes, and its unwrapped truth.
WRAPPED_BOX = Polygon([(179, 0), (-179, 0), (-179, 1), (179, 1)])
UNWRAPPED_BOX = box(179, 0, 181, 1)


def test_wrapped_box_plus_a_box_at_lon_10_is_the_sum_of_both():
    far = box(10, 0, 11, 1)
    m = measure(MultiPolygon([WRAPPED_BOX, far]))
    assert m.area_m2 == pytest.approx(geod_area(UNWRAPPED_BOX) + geod_area(far), rel=1e-3)
    assert m.is_valid is True
    assert any("antimeridian" in w for w in m.warnings)


def test_box_with_a_hole_across_180_subtracts_the_hole():
    wrapped = Polygon(
        [(178, 0), (-178, 0), (-178, 4), (178, 4)],
        [[(-179.5, 1), (-178.5, 1), (-178.5, 2), (-179.5, 2)]],
    )
    truth = Polygon(
        [(178, 0), (182, 0), (182, 4), (178, 4)], [[(180.5, 1), (181.5, 1), (181.5, 2), (180.5, 2)]]
    )
    m = measure(wrapped)
    assert m.area_m2 == pytest.approx(geod_area(truth), rel=1e-3)
    assert m.area_m2 < geod_area(box(178, 0, 182, 4))
    assert m.is_valid is True


def test_hole_that_itself_crosses_180_is_valid_once_unwrapped():
    wrapped = Polygon(
        [(178, 0), (-178, 0), (-178, 4), (178, 4)],
        [[(179.5, 1), (-179.5, 1), (-179.5, 2), (179.5, 2)]],
    )
    truth = Polygon(
        [(178, 0), (182, 0), (182, 4), (178, 4)], [[(179.5, 1), (180.5, 1), (180.5, 2), (179.5, 2)]]
    )
    m = measure(wrapped)
    assert m.area_m2 == pytest.approx(geod_area(truth), rel=1e-3)
    assert m.is_valid is True


def test_bowtie_across_180_measures_like_the_same_bowtie_at_lon_0():
    across = measure(Polygon([(179, 0), (-179, 1), (-179, 0), (179, 1)]))
    at_zero = measure(Polygon([(0, 0), (2, 1), (2, 0), (0, 1)]))
    assert across.area_m2 == pytest.approx(at_zero.area_m2, rel=1e-3)
    assert across.is_valid is False


def test_bowtie_in_a_mixed_collection_is_repaired_like_the_bowtie_alone():
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
    alone = measure(bowtie)
    mixed = measure(GeometryCollection([bowtie, Point(5, 5)]))
    assert mixed.area_m2 == pytest.approx(alone.area_m2, rel=1e-9)
    assert mixed.is_valid is False


def test_identical_polygons_with_a_point_are_counted_once_with_a_warning():
    square = box(0, 0, 1, 1)
    m = measure(GeometryCollection([square, square, Point(5, 5)]))
    assert m.area_m2 == pytest.approx(geod_area(square), rel=1e-3)
    assert m.is_valid is False
    assert any("repair changed the area" in w for w in m.warnings)


def test_overlapping_polygons_in_one_multigeometry_are_measured_as_their_union():
    a, b = box(0, 0, 2, 2), box(1, 1, 3, 3)
    m = measure(MultiPolygon([a, b]))
    assert m.area_m2 == pytest.approx(geod_area(a.union(b)), rel=1e-3)
    assert m.is_valid is False
    assert any("repair changed the area" in w for w in m.warnings)


def test_valid_input_has_no_repair_warning():
    m = measure(MultiPolygon([box(0, 0, 1, 1), box(2, 0, 3, 1)]))
    assert m.is_valid is True
    assert not any("repair" in w for w in m.warnings)
