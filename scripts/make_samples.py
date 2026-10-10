"""Generate the sample and bad-input files in samples/ (deterministic; fictional coordinates).

python scripts/make_samples.py [output_dir]
"""

import io
import sys
import zipfile
from pathlib import Path

import shapefile
from pyproj import CRS, Transformer

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "samples"

# Expected HTTP status when each file is uploaded (used by tests and scripts/smoke.sh).
EXPECTED_STATUS = {
    "sample.kml": 201,
    "sample_shapefile.zip": 201,
    "bad_not_a_map.kml": 415,
    "bad_empty.kml": 400,
    "bad_truncated.kml": 400,
    "bad_corrupt.zip": 400,
    "bad_zip_slip.zip": 400,
    "bad_no_prj.zip": 422,
    "bad_coordinates.kml": 422,  # its only feature is out of range, so nothing is measurable
}

KML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2">\n'


def _placemark(name: str, body: str) -> str:
    return f"  <Placemark><name>{name}</name>{body}</Placemark>\n"


def _polygon(outer: str, hole: str | None = None) -> str:
    inner = (
        f"<innerBoundaryIs><LinearRing><coordinates>{hole}</coordinates></LinearRing>"
        "</innerBoundaryIs>"
        if hole
        else ""
    )
    return (
        "<Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{outer}</coordinates></LinearRing></outerBoundaryIs>{inner}</Polygon>"
    )


def sample_kml() -> bytes:
    body = (
        _placemark("Viewpoint", "<Point><coordinates>13.4050,52.5200,34</coordinates></Point>")
        + _placemark(
            "Footpath",
            "<LineString><coordinates>13.40,52.52 13.41,52.52 13.41,52.53</coordinates>"
            "</LineString>",
        )
        + _placemark(
            "Park with pond",
            _polygon(
                "13.40,52.50 13.42,52.50 13.42,52.51 13.40,52.51 13.40,52.50",
                "13.405,52.503 13.410,52.503 13.410,52.507 13.405,52.507 13.405,52.503",
            ),
        )
        + _placemark(
            "Cycle route and stop",
            "<MultiGeometry><Point><coordinates>13.43,52.50</coordinates></Point>"
            "<LineString><coordinates>13.43,52.50 13.44,52.51</coordinates></LineString>"
            "</MultiGeometry>",
        )
    )
    return (KML_HEAD + "<Document><name>Sample</name>\n" + body + "</Document>\n</kml>\n").encode()


def _fixed_zip(entries: dict[str, bytes]) -> bytes:
    """A zip with fixed timestamps, so regenerating gives identical bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


def _utm_shapefile_members() -> dict[str, bytes]:
    utm = CRS.from_epsg(32633)  # UTM 33N, covers the sample area
    fwd = Transformer.from_crs(4326, utm, always_xy=True)

    def ring(box):
        w, s, e, n = box
        pts = [(w, s), (w, n), (e, n), (e, s), (w, s)]  # clockwise: exterior ring
        return [fwd.transform(x, y) for x, y in pts]

    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON, encoding="utf-8")
    w.field("name", "C", 40)
    w.field("landuse", "C", 20)
    w.poly([ring((13.40, 52.50, 13.42, 52.51))])
    w.record("Field A", "farmland")
    w.poly([ring((13.43, 52.50, 13.44, 52.52))])
    w.record("Field B", "meadow")
    w.close()
    return {
        "sample.shp": shp.getvalue(),
        "sample.shx": shx.getvalue(),
        "sample.dbf": dbf.getvalue(),
        "sample.cpg": b"UTF-8",
        "sample.prj": utm.to_wkt().encode(),
    }


def generate(out_dir: Path = DEFAULT_DIR) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    members = _utm_shapefile_members()
    kml = sample_kml()
    files = {
        "sample.kml": kml,
        "sample_shapefile.zip": _fixed_zip(members),
        # bad inputs, one per failure path
        "bad_not_a_map.kml": b"\x89PNG\r\n\x1a\n" + bytes(range(64)),  # a PNG named .kml
        "bad_empty.kml": b"",
        "bad_truncated.kml": kml[: len(kml) // 2],
        "bad_corrupt.zip": b"PK\x03\x04" + b"this is not really a zip archive" * 4,
        "bad_zip_slip.zip": _fixed_zip({**members, "../../escape.shp": b"x"}),
        "bad_no_prj.zip": _fixed_zip({k: v for k, v in members.items() if k != "sample.prj"}),
        "bad_coordinates.kml": (
            KML_HEAD
            + _placemark("Out of range", "<Point><coordinates>400.0,95.0</coordinates></Point>")
            + "</kml>\n"
        ).encode(),
    }
    assert set(files) == set(EXPECTED_STATUS)
    for name, data in files.items():
        (out_dir / name).write_bytes(data)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR
    generate(target)
    print(f"wrote {len(EXPECTED_STATUS)} files to {target}")
