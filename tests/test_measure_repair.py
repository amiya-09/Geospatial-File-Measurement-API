"""Repair happens after unwrapping, and the polygons of a feature are repaired together.

References come from Geod with the edges cut every 0.01 degrees, so they follow the meridians
and parallels, and with rings oriented the way Geod expects (holes clockwise).
"""

import pytest
import shapely
from pyproj import Geod
from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon, box
from shapely.geometry.polygon import orient

from app import config
from app.geo.measure import MeasurementError, measure

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


# ---- repair cost limit ----


def star(n: int, r: float = 0.01) -> list[tuple[float, float]]:
    """A star polygon whose edges cross each other about n*n/4 times: the slowest repair found."""
    import math

    m = n // 2
    return [
        (r * math.cos(2 * math.pi * (k * m % n) / n), r * math.sin(2 * math.pi * (k * m % n) / n))
        for k in range(n)
    ]


def test_invalid_polygon_above_the_repair_limit_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "MAX_REPAIR_VERTICES", 20)
    with pytest.raises(MeasurementError, match="repair is limited to 20 vertices"):
        measure(Polygon(star(21)))


def test_invalid_polygon_at_the_repair_limit_is_repaired(monkeypatch):
    # Vertices are counted as stored, including each ring's closing vertex: 21 + 1.
    monkeypatch.setattr(config, "MAX_REPAIR_VERTICES", 22)
    m = measure(Polygon(star(21)))
    assert m.is_valid is False and m.area_m2 > 0


def test_repair_limit_counts_all_polygons_of_the_feature_together(monkeypatch):
    monkeypatch.setattr(config, "MAX_REPAIR_VERTICES", 8)
    overlapping = MultiPolygon([box(0, 0, 2, 2), box(1, 1, 3, 3)])  # 5 + 5 coordinates
    with pytest.raises(MeasurementError, match="repair is limited"):
        measure(overlapping)


def test_valid_polygon_above_the_repair_limit_is_measured(monkeypatch):
    monkeypatch.setattr(config, "MAX_REPAIR_VERTICES", 20)
    circle = Point(0, 0).buffer(0.01, quad_segs=64)
    assert circle.is_valid and len(circle.exterior.coords) > 20
    assert measure(circle).area_m2 > 0


def test_measurement_reports_how_many_parts_were_measured():
    assert measure(box(0, 0, 1, 1)).part_count == 1
    assert measure(MultiPolygon([box(0, 0, 1, 1), box(2, 0, 3, 1)])).part_count == 2
    assert measure(Point(0, 0)).part_count == 0
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
    assert measure(bowtie).part_count == 2  # repaired into two triangles
