"""Shapefile ZIP parser. The archive is validated and read entirely in memory."""

import codecs
import datetime
import io
import re
import struct
import zipfile
import zlib
from posixpath import splitext
from typing import Any

import shapefile
import shapely
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.errors import ShapelyError
from shapely.geometry import shape

from app.config import MAX_UNCOMPRESSED_BYTES, MAX_UPLOAD_BYTES, MAX_ZIP_ENTRIES
from app.parsers.base import Feature, ParsedFile, ParseError, UnmeasurableError

WGS84 = CRS.from_epsg(4326)

_ZIP_SIGNATURE = b"PK\x03\x04"
_MEMBER_EXTENSIONS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
_REQUIRED = ("shp", "shx", "dbf")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


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
    if not data.startswith(_ZIP_SIGNATURE):
        raise ParseError("not a ZIP archive (missing PK signature)")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
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
            ) as exc:
                raise ParseError(f"could not read {info.filename!r} from ZIP: {exc}") from exc
            budget -= len(blob)
            if budget < 0:
                raise ParseError(f"ZIP uncompressed size exceeds the {max_uncompressed} byte limit")
            result[ext] = blob
        return result


def _encoding_from_cpg(cpg: bytes | None, warnings: list[str]) -> str:
    """Codec named by the .cpg file, else UTF-8. An unrecognised name is warned about."""
    if cpg is None:
        return "utf-8"
    name = cpg.decode("ascii", errors="ignore").strip()
    if name.isdigit():
        name = f"cp{name}"  # Windows code page number, e.g. "1252"
    try:
        return codecs.lookup(name).name
    except LookupError:
        warnings.append(f"unrecognised encoding {name!r} in .cpg; assuming UTF-8")
        return "utf-8"


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
    members: dict[str, bytes | None], encoding: str
) -> tuple[list[Feature], list[str]]:
    features: list[Feature] = []
    warnings: list[str] = []
    with shapefile.Reader(
        shp=io.BytesIO(members["shp"]),
        shx=io.BytesIO(members["shx"]),
        dbf=io.BytesIO(members["dbf"]),
        encoding=encoding,
    ) as reader:
        for index, shape_record in enumerate(reader.iterShapeRecords()):
            geometry = None
            if shape_record.shape.shapeType == shapefile.NULL:
                warnings.append(f"feature {index}: null shape; no geometry")
            else:
                try:
                    geometry = shape(shape_record.shape.__geo_interface__)
                except (shapefile.GeoJSON_Error, ShapelyError, ValueError) as exc:
                    warnings.append(f"feature {index}: geometry could not be read ({exc})")
            properties = {k: _json_safe(v) for k, v in shape_record.record.as_dict().items()}
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


def parse_shapefile_zip(data: bytes, **limits: int) -> ParsedFile:
    """Parse a ZIP containing one shapefile. ``limits`` are passed to read_zip_members."""
    members = read_zip_members(data, **limits)
    warnings: list[str] = []
    encoding = _encoding_from_cpg(members["cpg"], warnings)
    try:
        try:
            features, read_warnings = _read_features(members, encoding)
        except (UnicodeDecodeError, shapefile.ShapefileException) as exc:
            if encoding == "latin-1" or not _is_decode_error(exc):
                raise
            warnings.append(f"attribute text is not valid {encoding}; decoded as latin-1")
            features, read_warnings = _read_features(members, "latin-1")
    except (
        shapefile.ShapefileException,
        struct.error,
        ValueError,
        IndexError,
        KeyError,
        EOFError,
        OverflowError,
    ) as exc:
        raise ParseError(f"could not read the shapefile: {exc}") from exc
    warnings += read_warnings
    return ParsedFile(
        crs=_resolve_crs(members["prj"], features, warnings), features=features, warnings=warnings
    )
