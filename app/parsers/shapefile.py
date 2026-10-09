"""Shapefile ZIP parser. The archive is validated and read entirely in memory."""

import io
import re
import zipfile
import zlib
from posixpath import splitext

from app.config import MAX_UNCOMPRESSED_BYTES, MAX_UPLOAD_BYTES, MAX_ZIP_ENTRIES
from app.parsers.base import ParseError

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
