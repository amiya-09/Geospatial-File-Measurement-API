"""Generated test files. Nothing binary is checked in."""

import io
import zipfile
from collections.abc import Callable

import shapefile


def shapefile_parts(
    build: Callable[[shapefile.Writer], None],
    shape_type: int,
    fields: tuple[tuple[str, str, int, int], ...] = (("name", "C", 40, 0),),
    encoding: str = "utf-8",
) -> dict[str, bytes]:
    """Write a shapefile with pyshp into memory; returns {"shp": ..., "shx": ..., "dbf": ...}."""
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    writer = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shape_type, encoding=encoding)
    for name, kind, size, decimal in fields:
        writer.field(name, kind, size, decimal)
    build(writer)
    writer.close()
    return {"shp": shp.getvalue(), "shx": shx.getvalue(), "dbf": dbf.getvalue()}


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def shapefile_zip(
    parts: dict[str, bytes],
    stem: str = "data",
    prj: str | None = None,
    cpg: str | None = None,
    extra: dict[str, bytes] | None = None,
) -> bytes:
    entries = {f"{stem}.{ext}": data for ext, data in parts.items()}
    if prj is not None:
        entries[f"{stem}.prj"] = prj.encode()
    if cpg is not None:
        entries[f"{stem}.cpg"] = cpg.encode()
    entries.update(extra or {})
    return make_zip(entries)


def mark_entries_encrypted(zip_bytes: bytes) -> bytes:
    """Set the 'encrypted' general-purpose flag on every entry (local and central headers)."""
    data = bytearray(zip_bytes)
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        pos = data.find(signature)
        while pos != -1:
            data[pos + flag_offset] |= 0x01
            pos = data.find(signature, pos + 4)
    return bytes(data)


def forge_uncompressed_size(zip_bytes: bytes, declared: int) -> bytes:
    """Overwrite the declared uncompressed size in every local and central header."""
    data = bytearray(zip_bytes)
    for signature, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        pos = data.find(signature)
        while pos != -1:
            data[pos + offset : pos + offset + 4] = declared.to_bytes(4, "little")
            pos = data.find(signature, pos + 4)
    return bytes(data)


def mark_dbf_records_deleted(dbf: bytes, *indexes: int) -> bytes:
    """Flag DBF records as deleted ('*' in each record's first byte)."""
    data = bytearray(dbf)
    header_len = int.from_bytes(data[8:10], "little")
    record_len = int.from_bytes(data[10:12], "little")
    for index in indexes:
        data[header_len + index * record_len] = ord("*")
    return bytes(data)
