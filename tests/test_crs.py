import pytest
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, Polygon

from app.geo.crs import local_aeqd, local_laea, project_local, to_wgs84

WGS84 = CRS.from_epsg(4326)


def test_to_wgs84_is_identity_for_4326_and_keeps_lon_lat_order():
    out = to_wgs84(Point(10.0, 50.0), WGS84)
    assert (out.x, out.y) == pytest.approx((10.0, 50.0))


def test_to_wgs84_from_utm_returns_lon_lat_not_lat_lon():
    # UTM 31N: easting 500000 is the central meridian (lon 3 deg), northing 0 is the equator.
    out = to_wgs84(Point(500000, 0), CRS.from_epsg(32631))
    assert (out.x, out.y) == pytest.approx((3.0, 0.0), abs=1e-9)


def test_to_wgs84_from_web_mercator_roundtrips():
    fwd = Transformer.from_crs(4326, 3857, always_xy=True)
    x, y = fwd.transform(12.5, 41.9)
    out = to_wgs84(Point(x, y), CRS.from_epsg(3857))
    assert (out.x, out.y) == pytest.approx((12.5, 41.9), abs=1e-9)


def test_to_wgs84_drops_z_before_transforming():
    out = to_wgs84(Point(10.0, 50.0, 1234.0), WGS84)
    assert not out.has_z


def test_local_laea_is_equal_area_projected_in_metres_centred_on_input():
    crs = local_laea(lon=10.0, lat=50.0)
    assert crs.is_projected
    assert crs.axis_info[0].unit_name == "metre"
    x, y = Transformer.from_crs(WGS84, crs, always_xy=True).transform(10.0, 50.0)
    assert (x, y) == pytest.approx((0.0, 0.0), abs=1e-6)


def test_local_aeqd_is_projected_in_metres_centred_on_input():
    crs = local_aeqd(lon=-120.0, lat=-45.0)
    assert crs.is_projected
    assert crs.axis_info[0].unit_name == "metre"
    x, y = Transformer.from_crs(WGS84, crs, always_xy=True).transform(-120.0, -45.0)
    assert (x, y) == pytest.approx((0.0, 0.0), abs=1e-6)


def test_project_local_centres_projection_on_geometry_centroid():
    poly = Polygon([(20, 40), (21, 40), (21, 41), (20, 41)])
    projected, _ = project_local(poly, kind="laea")
    cx, cy = projected.centroid.x, projected.centroid.y
    # ~0.5 deg off-centre at most, so well within 100 km of the origin
    assert abs(cx) < 100_000 and abs(cy) < 100_000


def test_project_local_rejects_unknown_kind():
    with pytest.raises(ValueError):
        project_local(LineString([(0, 0), (1, 1)]), kind="utm")


def test_project_local_returns_the_crs_it_used():
    poly = Polygon([(20, 40), (21, 40), (21, 41), (20, 41)])
    _, crs = project_local(poly, kind="aeqd")
    assert "+proj=aeqd" in crs.srs
    assert "+lon_0=20.5" in crs.srs


@pytest.mark.parametrize(
    ("lon", "expected"),
    [(190.5, "-169.5"), (360.0, "0.0"), (-181.0, "179.0"), (45.0, "45.0")],
)
def test_local_projections_normalise_lon_0_into_minus_180_180(lon, expected):
    assert f"+lon_0={expected} " in local_laea(lon=lon, lat=0.0).srs
    assert f"+lon_0={expected} " in local_aeqd(lon=lon, lat=0.0).srs
