"""Shapefile ZIP parser. The archive is validated and read entirely in memory."""

import codecs
import datetime
import io
import math
import re
import struct
import zipfile
import zlib
from dataclasses import replace
from posixpath import splitext
from typing import Any

import shapefile
import shapely
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.errors import ShapelyError
from shapely.geometry import shape

from app.config import (
    MAX_FEATURES,
    MAX_PARTS,
    MAX_UNCOMPRESSED_BYTES,
    MAX_UPLOAD_BYTES,
    MAX_VERTICES,
    MAX_ZIP_ENTRIES,
)
from app.parsers.base import Budget, Feature, ParsedFile, ParseError, UnmeasurableError

WGS84 = CRS.from_epsg(4326)

ZIP_SIGNATURE = b"PK\x03\x04"
_MEMBER_EXTENSIONS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
_REQUIRED = ("shp", "shx", "dbf")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
# Text encodings a .dbf can use, by their codecs.lookup() name.
_DBF_ENCODINGS = {
    "utf-8", "utf-8-sig", "ascii", "iso8859-1", "iso8859-2", "iso8859-3", "iso8859-4",
    "iso8859-5", "iso8859-6", "iso8859-7", "iso8859-8", "iso8859-9", "iso8859-10",
    "iso8859-11", "iso8859-13", "iso8859-14", "iso8859-15", "iso8859-16", "cp437", "cp737",
    "cp775", "cp850", "cp852", "cp855", "cp857", "cp860", "cp861", "cp862", "cp863", "cp864",
    "cp865", "cp866", "cp869", "cp874", "cp932", "cp949", "cp950", "cp1250", "cp1251",
    "cp1252", "cp1253", "cp1254", "cp1255", "cp1256", "cp1257", "cp1258", "koi8-r", "koi8-u",
    "shift_jis", "euc_jp", "euc_kr", "gbk", "gb2312", "gb18030", "big5", "mac-roman",
}  # fmt: skip
_POINT_TYPES = {
    shapefile.POINT,
    shapefile.POINTZ,
    shapefile.POINTM,
    shapefile.MULTIPOINT,
    shapefile.MULTIPOINTZ,
    shapefile.MULTIPOINTM,
}


def _is_unsafe(name: str) -> bool:
    """Absolute, drive-lettered, NUL-containing or parent-traversing entry names."""
    normalised = name.replace("\\", "/")
    return (
        normalised.startswith("/")
        or "\x00" in normalised
        or bool(_DRIVE_PREFIX.match(normalised))
        or ".." in normalised.split("/")
    )


def read_zip_members(
    data: bytes,
    *,
    max_compressed: int = MAX_UPLOAD_BYTES,
    max_uncompressed: int = MAX_UNCOMPRESSED_BYTES,
    max_entries: int = MAX_ZIP_ENTRIES,
) -> dict[str, bytes | None]:
    """Validate a Shapefile ZIP and return its shp/shx/dbf (and optional prj/cpg) bytes.

    Nothing is extracted to disk. Raises ParseError for anything unreadable or unsafe.
    """
    if len(data) > max_compressed:
        raise ParseError(f"file is too large ({len(data)} bytes, limit {max_compressed})")
    if not data.startswith(ZIP_SIGNATURE):
        raise ParseError("not a ZIP archive (missing PK signature)")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, NotImplementedError, ValueError, EOFError, OverflowError) as exc:
        raise ParseError(f"not a readable ZIP archive: {exc}") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > max_entries:
            raise ParseError(f"ZIP has too many entries ({len(infos)}, limit {max_entries})")
        if sum(i.file_size for i in infos) > max_uncompressed:
            raise ParseError(f"ZIP uncompressed size exceeds the {max_uncompressed} byte limit")

        groups: dict[tuple[str, str], dict[str, zipfile.ZipInfo]] = {}
        for info in infos:
            if _is_unsafe(info.filename):
                raise ParseError(f"unsafe path in ZIP: {info.filename!r}")
            if info.flag_bits & 0x1:
                raise ParseError("ZIP contains encrypted entries")
            parts = info.filename.replace("\\", "/").lower().split("/")
            if info.is_dir() or "__macosx" in parts:
                continue
            stem, ext = splitext(parts[-1])
            if ext not in _MEMBER_EXTENSIONS:
                continue
            members = groups.setdefault(("/".join(parts[:-1]), stem), {})
            if ext[1:] in members:
                raise ParseError(f"duplicate entry in ZIP: {info.filename!r}")
            members[ext[1:]] = info

        shapefiles = [members for members in groups.values() if "shp" in members]
        if not shapefiles:
            raise ParseError("ZIP contains no .shp file")
        if len(shapefiles) > 1:
            raise ParseError("ZIP contains multiple shapefiles; upload one shapefile per ZIP")
        members = shapefiles[0]
        for ext in _REQUIRED:
            if ext not in members:
                raise ParseError(f"shapefile is missing its .{ext} file")

        budget = max_uncompressed
        result: dict[str, bytes | None] = {"prj": None, "cpg": None}
        for ext, info in members.items():
            try:
                with archive.open(info) as handle:
                    blob = handle.read(budget + 1)
            except (
                RuntimeError,
                NotImplementedError,
                zipfile.BadZipFile,
                zlib.error,
                EOFError,
                ValueError,  # e.g. a corrupt header offset (negative seek)
            ) as exc:
                raise ParseError(f"could not read {info.filename!r} from ZIP: {exc}") from exc
            budget -= len(blob)
            if budget < 0:
                raise ParseError(f"ZIP uncompressed size exceeds the {max_uncompressed} byte limit")
            result[ext] = blob
        return result


def _encoding_from_cpg(cpg: bytes | None, warnings: list[str]) -> str:
    """Codec named by the .cpg file, else UTF-8. An unrecognised name is warned about.

    Only byte-oriented text encodings are accepted: codecs.lookup also knows base64, rot13,
    zlib and UTF-16, which cannot decode a .dbf.
    """
    if cpg is None:
        return "utf-8"
    name = cpg.decode("ascii", errors="ignore").strip()
    if name.isdigit():
        name = f"cp{name}"  # Windows code page number, e.g. "1252"
    try:
        canonical = codecs.lookup(name).name
    except (LookupError, ValueError):  # unknown codec name, or a name with a NUL byte
        canonical = None
    if canonical not in _DBF_ENCODINGS:
        warnings.append(f"unrecognised encoding {name!r} in .cpg; assuming UTF-8")
        return "utf-8"
    return canonical


def _is_decode_error(exc: BaseException) -> bool:
    """pyshp reports a text decoding failure as a dbfFileException with this message."""
    return isinstance(exc, UnicodeDecodeError) or (
        isinstance(exc, shapefile.dbfFileException) and "Could not decode" in str(exc)
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime.date | datetime.datetime):
        return value.isoformat()
    return value


def _read_features(
    members: dict[str, bytes | None],
    encoding: str,
    max_features: int,
    max_vertices: int,
    max_parts: int,
) -> tuple[list[Feature], list[str]]:
    features: list[Feature] = []
    warnings: list[str] = []
    budget = Budget(max_vertices, "vertices")
    parts = Budget(max_parts, "parts")
    with shapefile.Reader(
        shp=io.BytesIO(members["shp"]),
        shx=io.BytesIO(members["shx"]),
        dbf=io.BytesIO(members["dbf"]),
        encoding=encoding,
    ) as reader:
        if reader.numShapes != reader.numRecords:
            raise ParseError(
                f"shapefile has {reader.numShapes} shapes but its .dbf has "
                f"{reader.numRecords} records; the files do not belong together"
            )
        if len(reader) > max_features:
            raise ParseError(f"file has more than {max_features} features")
        # Read by position, not with iterShapeRecords(): that skips records flagged deleted in
        # the .dbf without skipping the matching shape, which pairs shapes with the wrong rows.
        for index in range(reader.numShapes):
            record = reader.record(index)
            if record is None:  # pyshp returns None for a record flagged deleted
                warnings.append(f"feature {index}: record is flagged deleted in the .dbf; skipped")
                continue
            raw_shape = reader.shape(index)
            raw_points = getattr(raw_shape, "points", ())
            budget.take(len(raw_points))
            if raw_shape.shapeType not in _POINT_TYPES:
                parts.take(len(getattr(raw_shape, "parts", ())))  # lines or polygon rings
            geometry = None
            if raw_shape.shapeType == shapefile.NULL:
                warnings.append(f"feature {index}: null shape; no geometry")
            elif not all(math.isfinite(c) for point in raw_points for c in point):
                warnings.append(f"feature {index}: non-finite (NaN/inf) coordinate; no geometry")
            else:
                try:
                    geometry = shape(raw_shape.__geo_interface__)
                except (shapefile.GeoJSON_Error, ShapelyError, ValueError) as exc:
                    warnings.append(f"feature {index}: geometry could not be read ({exc})")
            properties = {k: _json_safe(v) for k, v in record.as_dict().items()}
            features.append(Feature(index, None, geometry, properties))
    return features, warnings


def _resolve_crs(prj: bytes | None, features: list[Feature], warnings: list[str]) -> CRS:
    if prj is not None:
        try:
            return CRS.from_wkt(prj.decode("utf-8", errors="replace"))
        except CRSError as exc:
            raise UnmeasurableError(f"could not interpret the .prj file: {exc}") from exc
    # No .prj: only safe to assume WGS84 if every coordinate could be a lon/lat degree.
    geoms = [f.geometry for f in features if f.geometry is not None and not f.geometry.is_empty]
    if geoms:
        minx, miny, maxx, maxy = shapely.total_bounds(geoms)
        if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
            raise UnmeasurableError(
                "no .prj file and coordinates are outside the lon/lat range, "
                "so the coordinate system cannot be determined"
            )
    warnings.append("no .prj file found; assumed EPSG:4326 because all coordinates fit lon/lat")
    return WGS84


def parse_shapefile_zip(
    data: bytes,
    *,
    max_compressed: int = MAX_UPLOAD_BYTES,
    max_uncompressed: int = MAX_UNCOMPRESSED_BYTES,
    max_entries: int = MAX_ZIP_ENTRIES,
    max_features: int = MAX_FEATURES,
    max_vertices: int = MAX_VERTICES,
    max_parts: int = MAX_PARTS,
) -> ParsedFile:
    """Parse a ZIP containing one shapefile."""
    members = read_zip_members(
        data,
        max_compressed=max_compressed,
        max_uncompressed=max_uncompressed,
        max_entries=max_entries,
    )
    warnings: list[str] = []
    encoding = _encoding_from_cpg(members["cpg"], warnings)
    try:
        try:
            features, read_warnings = _read_features(
                members, encoding, max_features, max_vertices, max_parts
            )
        except (UnicodeDecodeError, shapefile.ShapefileException) as exc:
            if encoding == "latin-1" or not _is_decode_error(exc):
                raise
            warnings.append(f"attribute text is not valid {encoding}; decoded as latin-1")
            features, read_warnings = _read_features(
                members, "latin-1", max_features, max_vertices, max_parts
            )
    except ParseError:
        raise
    except (
        shapefile.ShapefileException,
        struct.error,
        ValueError,
        IndexError,
        KeyError,
        EOFError,
        OverflowError,
        AssertionError,  # pyshp asserts on an .shx that disagrees with its own header
    ) as exc:
        raise ParseError(f"could not read the shapefile: {exc}") from exc
    warnings += read_warnings
    crs = _resolve_crs(members["prj"], features, warnings)
    if crs.is_geographic and all(a.unit_name.lower() == "degree" for a in crs.axis_info):
        features = [_drop_if_out_of_range(f, warnings) for f in features]
    return ParsedFile(crs=crs, features=features, warnings=warnings)


def _drop_if_out_of_range(feature: Feature, warnings: list[str]) -> Feature:
    """For lon/lat data: a coordinate past +/-180 or +/-90 makes the geometry unusable."""
    if feature.geometry is None or feature.geometry.is_empty:
        return feature
    minx, miny, maxx, maxy = feature.geometry.bounds
    if -180 <= minx and maxx <= 180 and -90 <= miny and maxy <= 90:
        return feature
    warnings.append(
        f"feature {feature.index}: coordinates are out of the lon/lat range; no geometry"
    )
    return replace(feature, geometry=None)
