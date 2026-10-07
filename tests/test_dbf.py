import builtins
import hashlib
import logging

from tests.dbf_fixtures import write_dbf


def test_read_only_reader_reads_fields_offsets_and_deleted_marker(tmp_path):
    from clinic_queue.dbf import DBFReader

    path = write_dbf(
        tmp_path / "RG011M1.DBF",
        [
            ("NUM", "C", 6),
            ("TETDAY", "D", 8),
            ("OVER", "C", 1),
            ("TREAT", "C", 1),
        ],
        [
            {"NUM": "100001", "TETDAY": "20261008", "OVER": "", "TREAT": "N"},
            {"_DELETED": True, "NUM": "100002", "TETDAY": "20261008", "OVER": "T", "TREAT": "Y"},
        ],
    )
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    reader = DBFReader(path)
    header = reader.read_header()
    first = reader.read_record(1)
    second = reader.read_record(2)

    assert header.record_count == 2
    assert header.record_length == 1 + 6 + 8 + 1 + 1
    assert first.recno == 1
    assert first.value("NUM") == "100001"
    assert first.value("TETDAY") == "20261008"
    assert first.value("TREAT") == "N"
    assert second.recno == 2
    assert second.deleted is True
    assert second.value("NUM") == "100002"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source_hash


def test_dbf_source_is_opened_only_with_binary_read_mode(tmp_path, monkeypatch):
    from clinic_queue.dbf import DBFReader

    path = write_dbf(tmp_path / "source.DBF", [("NUM", "C", 6)], [{"NUM": "100001"}])
    original_open = builtins.open
    modes = []

    def observe_open(file, mode="r", *args, **kwargs):
        modes.append(mode)
        return original_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", observe_open)

    records = list(DBFReader(path).iter_records())

    assert len(records) == 1
    assert modes
    assert set(modes) == {"rb"}


def test_reader_preserves_numeric_logical_and_character_field_values(tmp_path):
    from clinic_queue.dbf import DBFReader

    path = write_dbf(
        tmp_path / "fields.DBF",
        [("NUM", "N", 5), ("ACTIVE", "L", 1), ("NAME", "C", 12)],
        [{"NUM": 42, "ACTIVE": True, "NAME": "Clinic"}],
    )

    record = DBFReader(path).read_record(1)

    assert record.value("NUM") == "42"
    assert record.value("ACTIVE") == "T"
    assert record.value("NAME") == "Clinic"


def test_malformed_record_is_logged_and_later_scan_continues(tmp_path, caplog):
    path = write_dbf(
        tmp_path / "source.DBF",
        [("NUM", "C", 6)],
        [{"NUM": "100001"}, {"NUM": "100002"}, {"NUM": "100003"}],
    )
    header_size = 32 + 32 + 1
    record_size = 1 + 6
    path.write_bytes(path.read_bytes()[: header_size + record_size + 3])

    from clinic_queue.dbf import DBFReader

    with caplog.at_level(logging.WARNING, logger="clinic_queue.dbf"):
        records = list(DBFReader(path).iter_records())

    assert [record.value("NUM") for record in records] == ["100001"]
    assert "record 2" in caplog.text
