"""Synthetic DBF writer used only by tests; never points at clinic HIS files."""

from __future__ import annotations

import struct
from datetime import date
from pathlib import Path


def write_dbf(path: Path, fields: list[tuple[str, str, int]], rows: list[dict[str, object]]) -> Path:
    """Write a small dBASE-compatible fixture for read-only adapter tests."""
    header_length = 32 + 32 * len(fields) + 1
    record_length = 1 + sum(width for _, _, width in fields)
    header = bytearray(32)
    header[0] = 0x03
    today = date.today()
    header[1:4] = bytes((today.year - 1900, today.month, today.day))
    struct.pack_into("<I", header, 4, len(rows))
    struct.pack_into("<H", header, 8, header_length)
    struct.pack_into("<H", header, 10, record_length)

    descriptors = bytearray()
    for name, kind, width in fields:
        descriptor = bytearray(32)
        descriptor[: len(name)] = name.encode("ascii")
        descriptor[11] = ord(kind)
        struct.pack_into("<I", descriptor, 12, 1 + sum(w for _, _, w in fields[: len(descriptors) // 32]))
        descriptor[16] = width
        descriptors.extend(descriptor)

    records = bytearray()
    for row in rows:
        deleted = bool(row.get("_DELETED", False))
        records.append(ord("*") if deleted else ord(" "))
        for name, kind, width in fields:
            value = row.get(name, "")
            if kind == "L":
                text = "T" if value is True else "F" if value is False else str(value or "?")
            elif isinstance(value, date):
                text = value.strftime("%Y%m%d")
            else:
                text = str(value)
            encoded = text.encode("cp950")
            if len(encoded) > width:
                raise ValueError(f"Fixture value for {name} exceeds its {width}-byte field")
            if kind in ("N", "F"):
                encoded = encoded.rjust(width, b" ")
            else:
                encoded = encoded.ljust(width, b" ")
            records.extend(encoded)

    path.write_bytes(bytes(header + descriptors + b"\r" + records + b"\x1a"))
    return path
