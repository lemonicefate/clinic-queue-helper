"""Small, read-only dBASE/Visual FoxPro DBF reader for the fields we use."""

from __future__ import annotations

import struct
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator


logger = logging.getLogger("clinic_queue.dbf")


class DBFReadError(OSError):
    """Raised when a DBF header or physical record cannot be read safely."""


@dataclass(frozen=True)
class DBFField:
    name: str
    kind: str
    width: int
    decimals: int
    offset: int


@dataclass(frozen=True)
class DBFHeader:
    record_count: int
    header_length: int
    record_length: int
    fields: tuple[DBFField, ...]


@dataclass(frozen=True)
class DBFRecord:
    recno: int
    deleted: bool
    raw_fields: dict[str, str]

    def raw_value(self, name: str, default: str = "") -> str:
        return self.raw_fields.get(name.upper(), default)

    def value(self, name: str, default: str = "") -> str:
        return self.raw_value(name, default).strip(" \x00")


class DBFReader:
    """Read DBF headers and fixed-offset records without any write handle."""

    def __init__(self, path: str | Path, encoding: str = "cp950") -> None:
        self.path = Path(path)
        self.encoding = encoding

    def read_header(self) -> DBFHeader:
        try:
            with open(self.path, "rb") as source:
                fixed_header = source.read(32)
                if len(fixed_header) != 32:
                    raise DBFReadError(f"DBF header is shorter than 32 bytes: {self.path}")
                record_count = struct.unpack_from("<I", fixed_header, 4)[0]
                header_length = struct.unpack_from("<H", fixed_header, 8)[0]
                record_length = struct.unpack_from("<H", fixed_header, 10)[0]
                if header_length < 33 or record_length < 1:
                    raise DBFReadError(f"Invalid DBF header or record length: {self.path}")
                source.seek(0)
                header_bytes = source.read(header_length)
        except OSError as exc:
            if isinstance(exc, DBFReadError):
                raise
            raise DBFReadError(f"Cannot read DBF header from {self.path}: {exc}") from exc

        if len(header_bytes) != header_length:
            raise DBFReadError(f"Incomplete DBF header: {self.path}")

        fields: list[DBFField] = []
        cursor = 32
        record_offset = 1
        while cursor < header_length:
            if header_bytes[cursor] == 0x0D:
                break
            descriptor = header_bytes[cursor : cursor + 32]
            if len(descriptor) != 32:
                raise DBFReadError(f"Incomplete DBF field descriptor: {self.path}")
            name_bytes = descriptor[:11].split(b"\x00", maxsplit=1)[0]
            try:
                name = name_bytes.decode("ascii").strip()
                kind = chr(descriptor[11])
            except (UnicodeDecodeError, ValueError) as exc:
                raise DBFReadError(f"Invalid DBF field descriptor at byte {cursor}: {self.path}") from exc
            width = descriptor[16]
            decimals = descriptor[17]
            if not name or width < 1:
                raise DBFReadError(f"Invalid DBF field name or width at byte {cursor}: {self.path}")
            fields.append(DBFField(name=name.upper(), kind=kind, width=width, decimals=decimals, offset=record_offset))
            record_offset += width
            cursor += 32

        if cursor >= header_length or header_bytes[cursor] != 0x0D:
            raise DBFReadError(f"DBF field terminator is missing: {self.path}")
        if record_offset > record_length:
            raise DBFReadError(f"DBF fields exceed the declared record length: {self.path}")

        return DBFHeader(
            record_count=record_count,
            header_length=header_length,
            record_length=record_length,
            fields=tuple(fields),
        )

    def read_record(self, recno: int, header: DBFHeader | None = None) -> DBFRecord:
        header = header or self.read_header()
        if type(recno) is not int or not 1 <= recno <= header.record_count:
            raise DBFReadError(f"Record number {recno} is outside the DBF record range: {self.path}")
        offset = header.header_length + (recno - 1) * header.record_length
        try:
            with open(self.path, "rb") as source:
                source.seek(offset)
                data = source.read(header.record_length)
        except OSError as exc:
            raise DBFReadError(f"Cannot read DBF record {recno} from {self.path}: {exc}") from exc
        return self._parse_record(data, recno, header)

    def iter_selected_records(
        self,
        recnos: Iterable[int],
        header: DBFHeader,
        *,
        record_errors: list[int] | None = None,
    ) -> Iterator[DBFRecord]:
        """Read fixed physical offsets through one shared read-only file handle."""
        try:
            with open(self.path, "rb") as source:
                for recno in recnos:
                    if type(recno) is not int or not 1 <= recno <= header.record_count:
                        if record_errors is not None:
                            record_errors.append(recno)
                        logger.warning("Skipping out-of-range DBF record %s in %s", recno, self.path)
                        continue
                    offset = header.header_length + (recno - 1) * header.record_length
                    source.seek(offset)
                    data = source.read(header.record_length)
                    try:
                        yield self._parse_record(data, recno, header)
                    except DBFReadError as exc:
                        if record_errors is not None:
                            record_errors.append(recno)
                        logger.warning("Skipping malformed DBF record %s in %s: %s", recno, self.path, exc)
        except OSError as exc:
            raise DBFReadError(f"Cannot read selected records from {self.path}: {exc}") from exc

    def iter_records(
        self,
        start: int = 1,
        end: int | None = None,
        *,
        header: DBFHeader | None = None,
        field_names: Iterable[str] | None = None,
        record_errors: list[int] | None = None,
    ) -> Iterator[DBFRecord]:
        header = header or self.read_header()
        selected_fields = (
            None if field_names is None else {field_name.upper() for field_name in field_names}
        )
        last = header.record_count if end is None else min(end, header.record_count)
        if type(start) is not int or start < 1:
            raise DBFReadError(f"Record scan start must be a positive record number: {start}")
        if last < start:
            return
        try:
            with open(self.path, "rb") as source:
                for recno in range(start, last + 1):
                    offset = header.header_length + (recno - 1) * header.record_length
                    try:
                        if selected_fields is None:
                            source.seek(offset)
                            data = source.read(header.record_length)
                            record = self._parse_record(data, recno, header)
                        else:
                            record = self._read_selected_record(
                                source, offset, recno, header, selected_fields
                            )
                        yield record
                    except DBFReadError as exc:
                        if record_errors is not None:
                            record_errors.append(recno)
                        logger.warning("Skipping malformed DBF record %s in %s: %s", recno, self.path, exc)
        except OSError as exc:
            raise DBFReadError(f"Cannot scan DBF records from {self.path}: {exc}") from exc

    def _read_selected_record(
        self,
        source: BinaryIO,
        offset: int,
        recno: int,
        header: DBFHeader,
        field_names: set[str],
    ) -> DBFRecord:
        source.seek(offset)
        deleted_marker = source.read(1)
        if len(deleted_marker) != 1:
            raise DBFReadError(f"Incomplete DBF record {recno}: missing deletion marker")

        raw_fields: dict[str, str] = {}
        for field in header.fields:
            if field.name not in field_names:
                continue
            source.seek(offset + field.offset)
            value_bytes = source.read(field.width)
            if len(value_bytes) != field.width:
                raise DBFReadError(
                    f"Incomplete DBF record {recno}: field {field.name} is truncated"
                )
            codec = self.encoding if field.kind in {"C", "V", "Q", "M", "G", "P", "W"} else "ascii"
            raw_fields[field.name] = value_bytes.decode(codec, errors="replace")
        return DBFRecord(recno=recno, deleted=deleted_marker == b"*", raw_fields=raw_fields)

    def _parse_record(self, data: bytes, recno: int, header: DBFHeader) -> DBFRecord:
        if len(data) != header.record_length:
            raise DBFReadError(
                f"Incomplete DBF record {recno}: expected {header.record_length} bytes, received {len(data)}"
            )
        deleted = data[0:1] == b"*"
        raw_fields: dict[str, str] = {}
        for field in header.fields:
            start = field.offset
            value_bytes = data[start : start + field.width]
            codec = self.encoding if field.kind in {"C", "V", "Q", "M", "G", "P", "W"} else "ascii"
            raw_fields[field.name] = value_bytes.decode(codec, errors="replace")
        return DBFRecord(recno=recno, deleted=deleted, raw_fields=raw_fields)
