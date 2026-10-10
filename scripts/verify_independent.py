"""Independent check of the running API. Deliberately imports nothing from ``app``.

Reads samples/sample.kml (plain XML) and samples/sample_shapefile.zip (zipfile + pyshp),
computes area and length with pyproj.Geod (geodesic, on the ellipsoid), uploads the same files
to the API, and prints API value, Geod value and percent difference per measurable feature.

    python scripts/verify_independent.py [base_url]     (default http://127.0.0.1:8000)

Exits 1 if any difference exceeds TOLERANCE_PCT or a feature cannot be matched.
Note: Geod follows geodesic edges while the API projects straight edges, so a small
difference is expected; it grows with feature size.
"""

import io
import json
import sys
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import shapefile
from pyproj import CRS, Geod, Transformer

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
GEOD = Geod(ellps="WGS84")
TOLERANCE_PCT = 0.5


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def coords(text: str) -> list[tuple[float, float]]:
    return [tuple(map(float, t.split(",")[:2])) for t in text.split()]


def ring_area(points) -> float:
    xs, ys = zip(*points, strict=True)
    return abs(GEOD.polygon_area_perimeter(xs, ys)[0])


def line_length(points) -> float:
    xs, ys = zip(*points, strict=True)
    return GEOD.line_length(xs, ys)


# ---- KML: plain XML, no app code ----


def kml_features(path: Path):
    """Yield (index, type, area_m2 or None, length_m or None) per Placemark, in file order."""
    root = ET.parse(path).getroot()
    for index, pm in enumerate(el for el in root.iter() if local(el.tag) == "Placemark"):
        area = length = None
        kinds = []
        for el in pm.iter():
            name = local(el.tag)
            if name == "Polygon":
                rings = [
                    coords(c.text)
                    for c in el.iter()
                    if local(c.tag) == "coordinates"  # outer ring first, then inner rings
                ]
                a = ring_area(rings[0]) - sum(ring_area(r) for r in rings[1:])
                area = (area or 0) + a
                kinds.append("Polygon")
            elif name == "LineString":
                coordinate_text = next(c.text for c in el if local(c.tag) == "coordinates")
                length = (length or 0) + line_length(coords(coordinate_text))
                kinds.append("LineString")
            elif name == "Point":
                kinds.append("Point")
        yield index, "+".join(kinds) or "none", area, length


# ---- Shapefile ZIP: zipfile + pyshp, no app code ----


def shapefile_features(path: Path):
    with zipfile.ZipFile(path) as zf:
        names = {n.rsplit(".", 1)[-1].lower(): n for n in zf.namelist()}
        parts = {ext: io.BytesIO(zf.read(names[ext])) for ext in ("shp", "shx", "dbf")}
        crs = CRS.from_wkt(zf.read(names["prj"]).decode())
    to_lonlat = Transformer.from_crs(crs, 4326, always_xy=True)
    reader = shapefile.Reader(**parts)
    for index, shp in enumerate(reader.shapes()):
        if shp.shapeType == 0:
            continue
        starts = list(shp.parts) + [len(shp.points)]
        rings = [shp.points[a:b] for a, b in zip(starts, starts[1:], strict=False)]
        if shp.shapeType in (5, 15, 25):  # polygon types
            ll = [[to_lonlat.transform(x, y) for x, y in r] for r in rings]
            yield index, "Polygon", sum(ring_area(r) for r in ll), None  # holes: none in sample
        elif shp.shapeType in (3, 13, 23):
            ll = [[to_lonlat.transform(x, y) for x, y in r] for r in rings]
            yield index, "LineString", None, sum(line_length(r) for r in ll)
    return crs.to_string()


# ---- API calls (stdlib only) ----


def upload(base: str, path: Path) -> dict:
    boundary = uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    )
    body = head.encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        f"{base}/api/files/",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def api_measurements(base: str, file_id: str) -> dict[int, dict]:
    url = f"{base}/api/files/{file_id}/measurements/?include_geometry=false"
    with urllib.request.urlopen(url) as response:
        return {f["index"]: f["measurement"] for f in json.load(response)["features"]}


def main(base: str) -> int:
    bad = 0
    print(f"{'file':<22}{'#':>2} {'type':<17}{'what':<7}{'API':>16}{'Geod':>16}{'diff %':>9}")
    for path, features in (
        (SAMPLES / "sample.kml", kml_features(SAMPLES / "sample.kml")),
        (SAMPLES / "sample_shapefile.zip", shapefile_features(SAMPLES / "sample_shapefile.zip")),
    ):
        info = upload(base, path)
        measured = api_measurements(base, info["id"])
        for index, kind, area, length in features:
            for what, truth, key in (("area", area, "area_m2"), ("length", length, "length_m")):
                if truth is None:
                    continue
                api = measured[index][key]
                if api is None:
                    print(f"{path.name:<22}{index:>2} {kind:<17}{what:<7}  API HAS NO VALUE")
                    bad += 1
                    continue
                diff = (api - truth) / truth * 100
                flag = "" if abs(diff) <= TOLERANCE_PCT else "  <-- over tolerance"
                bad += bool(flag)
                print(
                    f"{path.name:<22}{index:>2} {kind:<17}{what:<7}"
                    f"{api:>16,.2f}{truth:>16,.2f}{diff:>+9.4f}{flag}"
                )
    print(f"{'OK' if not bad else 'FAILED'}: tolerance {TOLERANCE_PCT}% (geodesic vs projected)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"))
