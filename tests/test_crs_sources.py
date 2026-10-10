"""A 0.1 degree box given in five different source CRSs measures the same as Geod.

The reference is Geod on the source CRS's own ellipsoid (GRS 1980 for NAD83 based CRSs).
EPSG:2263 and EPSG:4269 go through the NAD83 to WGS 84 (1) step, which pyproj lists with
about 4 m accuracy. It is a rigid shift, so the area barely moves: the measured gap is
about 3e-5 percent, not metres.
"""

import pytest
from pyproj import CRS, Transformer
from shapely.geometry import Polygon

from app.geo.crs import to_wgs84
from app.geo.measure import measure

LON_LAT_BOX = [(-73.95, 40.70), (-73.85, 40.70), (-73.85, 40.80), (-73.95, 40.80)]
UTM_BOX = [(15.0, 50.0), (15.1, 50.0), (15.1, 50.1), (15.0, 50.1)]


@pytest.mark.parametrize(
    ("epsg", "box"),
    [
        (4326, LON_LAT_BOX),
        (32633, UTM_BOX),
        (3857, LON_LAT_BOX),
        (2263, LON_LAT_BOX),
        (4269, LON_LAT_BOX),
    ],
)
def test_area_matches_geod_for_each_source_crs(epsg, box):
    crs = CRS.from_epsg(epsg)
    to_source = Transformer.from_crs(4326, crs, always_xy=True)
    ring = [to_source.transform(x, y) for x, y in box]
    ours = measure(to_wgs84(Polygon(ring), crs)).area_m2

    # Reference: go back to the source's geodetic CRS and measure on its own ellipsoid.
    to_geodetic = Transformer.from_crs(crs, crs.geodetic_crs, always_xy=True)
    lonlat = [to_geodetic.transform(x, y) for x, y in ring]
    lons, lats = zip(*lonlat, strict=True)
    reference = abs(crs.get_geod().polygon_area_perimeter(lons, lats)[0])

    assert ours == pytest.approx(reference, rel=1e-6)
