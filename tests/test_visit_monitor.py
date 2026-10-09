import asyncio
import json
import re
from datetime import date

import httpx
import pytest

from clinic_queue.app import create_app
from clinic_queue.config import ConfigurationError, load_config
from tests.dbf_fixtures import write_dbf


def write_config(tmp_path, *, enabled=False, visit_filename=None, doctor_room_map=None):
    his_path = tmp_path / "his"
    his_path.mkdir()
    values = {
        "his_data_path": str(his_path),
        "visit_monitor_enabled": enabled,
        "doctor_room_map": doctor_room_map or {},
        "patient_name_field": "",
        "sqlite_path": "state/queue.sqlite3",
        "log_path": "logs/app.log",
    }
    if visit_filename is not None:
        values["rg011m1_visit_filename"] = visit_filename
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(values), encoding="utf-8")
    return config_path, his_path


def write_rg_fixture(path, rows):
    today = date.today().strftime("%Y%m%d")
    write_dbf(
        path,
        [
            ("NUM", "C", 6),
            ("TETDAY", "D", 8),
            ("CCDATE", "D", 8),
            ("CCTIME", "C", 4),
            ("CCDOC", "C", 6),
            ("TIME_KIND", "C", 1),
            ("OVER", "C", 1),
            ("TREAT", "C", 1),
            ("GINO1", "C", 10),
            ("RELKEY", "C", 12),
        ],
        [
            {
                "NUM": number,
                "TETDAY": today,
                "CCDATE": today,
                "CCTIME": "0900",
                "CCDOC": doctor,
                "TIME_KIND": "1",
                "OVER": "",
                "TREAT": "N",
                "GINO1": queue_number,
                "RELKEY": key,
            }
            for key, number, doctor, queue_number in rows
        ],
    )


def write_visit_fixture(path, rows):
    write_dbf(
        path,
        [
            ("SYS_2015", "C", 12),
            ("NUM", "C", 6),
            ("CCDOC", "C", 6),
            ("SDATE", "D", 8),
            ("TIME_KIND", "C", 1),
            ("STIME", "C", 6),
        ],
        rows,
    )


async def get_json(app, path):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


def test_visit_monitor_is_disabled_by_default_and_uses_configurable_source(tmp_path):
    config_path, _ = write_config(tmp_path)

    config = load_config(config_path)

    assert config.visit_monitor_enabled is False
    assert config.rg011m1_visit_filename == "RG011M1_VISIT.DBF"
    assert config.visit_filename == "RG011M1_VISIT.DBF"


@pytest.mark.parametrize("value", [None, "true", 1, []])
def test_visit_monitor_enabled_must_be_boolean(tmp_path, value):
    config_path, _ = write_config(tmp_path)
    settings = json.loads(config_path.read_text(encoding="utf-8"))
    if value is None:
        settings["visit_monitor_enabled"] = None
    else:
        settings["visit_monitor_enabled"] = value
    config_path.write_text(json.dumps(settings), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="visit_monitor_enabled"):
        load_config(config_path)


def test_disabled_visit_monitor_does_not_read_or_render_visit_source(tmp_path):
    config_path, his_path = write_config(tmp_path)
    write_rg_fixture(his_path / "RG011M1.DBF", [])
    config = load_config(config_path)
    app = create_app(config)

    api_response = asyncio.run(get_json(app, "/api/visit-monitor"))
    page_response = asyncio.run(get_json(app, "/"))

    assert api_response.status_code == 200
    payload = api_response.json()
    assert payload["enabled"] is False
    assert payload["status"] == "DISABLED"
    assert payload["source_health"]["status"] == "DISABLED"
    assert payload["source_health"]["baseline_established"] is False
    assert payload["source_health"]["record_count"] == 0
    assert payload["source_health"]["error"] is None
    assert payload["baseline"] == {"established": False, "record_count": 0}
    assert payload["rooms"] == {
        "1": {"status": "DISABLED", "candidates": []},
        "2": {"status": "DISABLED", "candidates": []},
    }
    assert payload["unmatched"] == {"status": "DISABLED", "candidates": []}
    assert page_response.status_code == 200
    assert "看診中（實驗）" not in page_response.text


def test_enabled_visit_monitor_baselines_existing_rows_without_replay(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        visit_filename="CONTROLLED_VISIT.DBF",
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(his_path / "RG011M1.DBF", [("RG-001", "100001", "DOC1", "1510070001")])
    write_visit_fixture(
        his_path / "CONTROLLED_VISIT.DBF",
        [
            {
                "SYS_2015": "SYS-001",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090000",
            }
        ],
    )
    source_before = (his_path / "CONTROLLED_VISIT.DBF").read_bytes()
    app = create_app(load_config(config_path))

    response = asyncio.run(get_json(app, "/api/visit-monitor"))
    payload = response.json()

    assert response.status_code == 200
    assert payload["enabled"] is True
    assert payload["status"] == "NONE"
    assert payload["source_health"]["status"] == "OK"
    assert payload["source_health"]["baseline_established"] is True
    assert payload["source_health"]["record_count"] == 1
    assert payload["source_health"]["filename"] == "CONTROLLED_VISIT.DBF"
    assert payload["baseline"] == {"established": True, "record_count": 1}
    assert payload["rooms"]["1"] == {"status": "NONE", "candidates": []}
    assert payload["rooms"]["2"] == {"status": "NONE", "candidates": []}
    assert payload["unmatched"] == {"status": "NONE", "candidates": []}
    assert (his_path / "CONTROLLED_VISIT.DBF").read_bytes() == source_before


def test_enabled_visit_monitor_reports_source_error_without_fabricating_empty_state(tmp_path):
    config_path, his_path = write_config(tmp_path, enabled=True)
    write_rg_fixture(his_path / "RG011M1.DBF", [])
    app = create_app(load_config(config_path))

    response = asyncio.run(get_json(app, "/api/visit-monitor"))
    payload = response.json()
    page_response = asyncio.run(get_json(app, "/"))

    assert response.status_code == 200
    assert payload["enabled"] is True
    assert payload["status"] == "ERROR"
    assert payload["source_health"]["status"] == "ERROR"
    assert payload["source_health"]["baseline_established"] is False
    assert payload["source_health"]["error"]
    assert all(room["status"] == "ERROR" for room in payload["rooms"].values())
    assert payload["unmatched"] == {"status": "ERROR", "candidates": []}
    assert page_response.status_code == 200
    assert "看診中（實驗）" in page_response.text
    assert "VISIT 資料來源錯誤" in page_response.text
    monitor_area = re.search(
        r'<section class="visit-monitor"[^>]*>.*?</section>',
        page_response.text,
        flags=re.DOTALL,
    )
    assert monitor_area is not None
    assert "data-action=" not in monitor_area.group(0)
    assert page_response.text.index('data-visit-monitor-room="1"') < page_response.text.index(
        ">候診中</span>"
    )


def test_enabled_empty_monitor_leaves_ordinary_queue_unchanged(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(his_path / "RG011M1.DBF", [("RG-001", "100001", "DOC1", "1510070001")])
    write_visit_fixture(his_path / "RG011M1_VISIT.DBF", [])
    app = create_app(load_config(config_path))

    response = asyncio.run(get_json(app, "/api/queue"))
    payload = response.json()

    assert response.status_code == 200
    assert [entry["patient_no"] for entry in payload["rooms"]["1"]["waiting"]] == ["100001"]
    assert payload["rooms"]["1"]["waiting"][0]["queue_position"] == 1
    assert payload["visit_monitor"]["status"] == "NONE"
