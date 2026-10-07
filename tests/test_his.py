import json
import logging
from datetime import date

from clinic_queue.config import load_config
from tests.dbf_fixtures import write_dbf


def test_today_scan_uses_tetday_and_excludes_deleted_records(tmp_path, caplog):
    his_path = tmp_path / "his"
    his_path.mkdir()
    write_dbf(
        his_path / "RG011M1.DBF",
        [
            ("NUM", "C", 6),
            ("TETDAY", "D", 8),
            ("CCDATE", "D", 8),
            ("CCTIME", "C", 4),
            ("CCDOC", "C", 6),
            ("OVER", "C", 1),
            ("TREAT", "C", 1),
            ("GINO1", "C", 10),
            ("RELKEY", "C", 12),
            ("SYS_2015", "C", 12),
        ],
        [
            {
                "NUM": "100001",
                "TETDAY": "20261008",
                "CCDATE": "20261001",
                "CCTIME": "0900",
                "CCDOC": "DOC1",
                "OVER": "",
                "TREAT": "N",
                "GINO1": "1510070053",
                "RELKEY": "REL-001",
            },
            {
                "NUM": "100002",
                "TETDAY": "20261009",
                "CCDATE": "20261008",
                "CCTIME": "0910",
                "CCDOC": "DOC1",
                "OVER": "",
                "TREAT": "N",
                "GINO1": "1510070054",
                "RELKEY": "REL-002",
            },
            {
                "_DELETED": True,
                "NUM": "100003",
                "TETDAY": "20261008",
                "CCDATE": "20261008",
                "CCTIME": "0920",
                "CCDOC": "DOC1",
                "OVER": "",
                "TREAT": "N",
                "GINO1": "1510070055",
                "RELKEY": "REL-003",
            },
        ],
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"his_data_path": str(his_path), "sqlite_path": "state/queue.sqlite3", "log_path": "logs/app.log"}),
        encoding="utf-8",
    )

    from clinic_queue.his import scan_today

    with caplog.at_level(logging.WARNING, logger="clinic_queue.his"):
        encounters = scan_today(load_config(config_path), date(2026, 10, 8))

    assert len(encounters) == 1
    encounter = encounters[0]
    assert encounter.patient_no == "100001"
    assert encounter.visit_date == date(2026, 10, 8)
    assert encounter.encounter_key == "relkey:REL-001"
    assert encounter.raw_his["TREAT"].strip() == "N"
    assert encounter.patient_name == "100001"
    assert "PD001M1.DBF" in caplog.text


def test_patient_lookup_uses_only_configured_number_and_name_fields(tmp_path):
    his_path = tmp_path / "his"
    his_path.mkdir()
    write_dbf(
        his_path / "RG011M1.DBF",
        [
            ("NUM", "C", 6),
            ("TETDAY", "D", 8),
            ("CCDATE", "D", 8),
            ("OVER", "C", 1),
            ("TREAT", "C", 1),
            ("RELKEY", "C", 12),
        ],
        [
            {
                "NUM": "100001",
                "TETDAY": "20261008",
                "CCDATE": "20261001",
                "OVER": "",
                "TREAT": "N",
                "RELKEY": "REL-001",
            }
        ],
    )
    write_dbf(
        his_path / "PD001M1.DBF",
        [("CHARTNO", "C", 6), ("FULLNAME", "C", 20), ("NATIONAL_ID", "C", 12)],
        [{"CHARTNO": "100001", "FULLNAME": "王小明", "NATIONAL_ID": "A123456789"}],
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "his_data_path": str(his_path),
                "patient_number_field": "CHARTNO",
                "patient_name_field": "FULLNAME",
                "sqlite_path": "state/queue.sqlite3",
                "log_path": "logs/app.log",
            }
        ),
        encoding="utf-8",
    )

    from clinic_queue.his import scan_today

    encounters = scan_today(load_config(config_path), date(2026, 10, 8))

    assert len(encounters) == 1
    assert encounters[0].patient_name == "王小明"
    assert encounters[0].patient_no == "100001"
    assert "NATIONAL_ID" not in encounters[0].raw_his


def test_missing_patient_master_keeps_encounter_visible_with_chart_number(tmp_path, caplog):
    his_path = tmp_path / "his"
    his_path.mkdir()
    write_dbf(
        his_path / "RG011M1.DBF",
        [("NUM", "C", 6), ("TETDAY", "D", 8), ("OVER", "C", 1), ("TREAT", "C", 1)],
        [{"NUM": "100001", "TETDAY": date.today().strftime("%Y%m%d"), "OVER": "", "TREAT": "N"}],
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "his_data_path": str(his_path),
                "patient_number_field": "CHARTNO",
                "patient_name_field": "FULLNAME",
                "sqlite_path": "state/queue.sqlite3",
                "log_path": "logs/app.log",
            }
        ),
        encoding="utf-8",
    )

    from clinic_queue.his import scan_today

    encounters = scan_today(load_config(config_path), date.today())

    assert len(encounters) == 1
    assert encounters[0].patient_name == "100001"
    assert "PD001M1.DBF" in caplog.text


def test_unknown_his_state_stays_visible_in_today_scan(tmp_path, caplog):
    his_path = tmp_path / "his"
    his_path.mkdir()
    write_dbf(
        his_path / "RG011M1.DBF",
        [("NUM", "C", 6), ("TETDAY", "D", 8), ("OVER", "C", 1), ("TREAT", "C", 1)],
        [{"NUM": "100001", "TETDAY": date.today().strftime("%Y%m%d"), "OVER": "?", "TREAT": "Z"}],
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"his_data_path": str(his_path), "sqlite_path": "state/queue.sqlite3", "log_path": "logs/app.log"}),
        encoding="utf-8",
    )

    from clinic_queue.his import scan_today

    encounters = scan_today(load_config(config_path), date.today())

    assert len(encounters) == 1
    assert encounters[0].active is True
    assert encounters[0].his_state.value == "WAITING"
    assert encounters[0].initial_presence.value == "PRESENT"
    assert "OVER='?'" in caplog.text
    assert "TREAT='Z'" in caplog.text
