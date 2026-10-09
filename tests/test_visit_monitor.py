import asyncio
import json
import re
from datetime import date, timedelta

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
    normalized_rows = []
    for row in rows:
        if isinstance(row, dict):
            normalized_rows.append(
                {
                    "NUM": row["NUM"],
                    "TETDAY": row.get("TETDAY", today),
                    "CCDATE": row.get("CCDATE", today),
                    "CCTIME": row.get("CCTIME", "0900"),
                    "CCDOC": row.get("CCDOC", "DOC1"),
                    "TIME_KIND": row.get("TIME_KIND", "1"),
                    "OVER": row.get("OVER", ""),
                    "TREAT": row.get("TREAT", "N"),
                    "GINO1": row.get("GINO1", "1510070001"),
                    "RELKEY": row.get("RELKEY", ""),
                    "SYS_2015": row.get("SYS_2015", ""),
                    "_DELETED": row.get("_DELETED", False),
                }
            )
            continue
        key, number, doctor, queue_number = row
        normalized_rows.append(
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
                "SYS_2015": "",
            }
        )
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
            ("SYS_2015", "C", 12),
        ],
        normalized_rows,
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


async def get_json(app, path, *, cookies=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies=cookies,
    ) as client:
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


def test_stable_appended_visit_record_becomes_unknown_candidate_after_two_reads(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(his_path / "RG011M1.DBF", [("RG-001", "100001", "DOC1", "1510070001")])
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))

    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090000",
            }
        ],
    )
    app.state.visit_monitor.poll_once()
    first_response = asyncio.run(get_json(app, "/api/visit-monitor"))
    first_payload = first_response.json()
    assert first_payload["rooms"]["1"]["candidates"] == []
    assert first_payload["pending"][0]["record_number"] == 1

    app.state.visit_monitor.poll_once()
    second_response = asyncio.run(get_json(app, "/api/visit-monitor"))
    payload = second_response.json()

    assert second_response.status_code == 200
    assert payload["status"] == "UNKNOWN"
    assert payload["rooms"]["1"]["status"] == "UNKNOWN"
    assert payload["rooms"]["1"]["candidates"] == [
        {
            "status": "UNKNOWN",
            "encounter_key": "relkey:RG-001",
            "patient_no": "100001",
            "patient_name": "100001",
            "doctor_code": "DOC1",
            "time_kind": 1,
            "clinic_session": "早診",
            "evidence_reason": "unique NUM+CCDOC+date+TIME_KIND fallback",
            "evidence_count": 1,
            "record_locators": [
                {"source": "RG011M1_VISIT.DBF", "record_number": 1}
            ],
        }
    ]
    assert payload["source_health"]["status"] == "OK"
    assert payload["source_health"]["record_count"] == 1

    page_response = asyncio.run(get_json(app, "/"))
    assert "UNKNOWN" in page_response.text
    assert "#100001" in page_response.text
    monitor_area = re.search(
        r'<section class="visit-monitor"[^>]*>.*?</section>',
        page_response.text,
        flags=re.DOTALL,
    )
    assert monitor_area is not None
    assert "data-action=" not in monitor_area.group(0)


def test_completed_exact_encounter_suppresses_current_and_reopen_detail_visit_noise(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [
            {
                "NUM": "100001",
                "CCDOC": "DOC1",
                "RELKEY": "REL-001",
                "SYS_2015": "SYS-001",
            }
        ],
    )
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))

    first_visit = {
        "SYS_2015": "SYS-001",
        "NUM": "100001",
        "CCDOC": "DOC1",
        "SDATE": date.today(),
        "TIME_KIND": "1",
        "STIME": "090000",
    }
    write_visit_fixture(visit_path, [first_visit])
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()
    assert asyncio.run(get_json(app, "/api/visit-monitor")).json()["rooms"]["1"]["candidates"]

    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [
            {
                "NUM": "100001",
                "CCDOC": "DOC1",
                "RELKEY": "REL-001",
                "SYS_2015": "SYS-001",
                "OVER": "T",
            }
        ],
    )
    app.state.his_poller.poll_once()

    reopen_detail_noise = {
        **first_visit,
        "STIME": "090001",
    }
    write_visit_fixture(visit_path, [first_visit, reopen_detail_noise])
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()

    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()

    assert payload["rooms"]["1"]["candidates"] == []
    assert [diagnostic["record_number"] for diagnostic in payload["diagnostics"]] == [1, 2]
    assert all(
        diagnostic["reason"] == "associated encounter is already HIS-derived COMPLETED"
        for diagnostic in payload["diagnostics"]
    )


def test_repeated_valid_visit_evidence_coalesces_and_retains_all_locators(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [
            {
                "NUM": "100001",
                "CCDOC": "DOC1",
                "RELKEY": "REL-001",
                "SYS_2015": "SYS-001",
            }
        ],
    )
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))

    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "SYS-001",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090000",
            },
            {
                "SYS_2015": "SYS-001",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090001",
            },
        ],
    )
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()

    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()

    assert payload["rooms"]["1"]["candidates"] == [
        {
            "status": "UNKNOWN",
            "encounter_key": "relkey:REL-001",
            "patient_no": "100001",
            "patient_name": "100001",
            "doctor_code": "DOC1",
            "time_kind": 1,
            "clinic_session": "早診",
            "evidence_reason": "exact SYS_2015 association",
            "evidence_count": 2,
            "record_locators": [
                {"source": "RG011M1_VISIT.DBF", "record_number": 1},
                {"source": "RG011M1_VISIT.DBF", "record_number": 2},
            ],
        }
    ]


def test_distinct_candidates_for_one_physician_session_are_all_ambiguous(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [
            {
                "NUM": "100001",
                "CCDOC": "DOC1",
                "RELKEY": "REL-A",
                "SYS_2015": "SYS-A",
            },
            {
                "NUM": "100002",
                "CCDOC": "DOC1",
                "RELKEY": "REL-B",
                "SYS_2015": "SYS-B",
            },
        ],
    )
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))

    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "SYS-A",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090001",
            },
            {
                "SYS_2015": "SYS-B",
                "NUM": "100002",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090000",
            },
        ],
    )
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()

    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    candidates = payload["rooms"]["1"]["candidates"]

    assert payload["status"] == "AMBIGUOUS"
    assert payload["rooms"]["1"]["status"] == "AMBIGUOUS"
    assert {candidate["encounter_key"] for candidate in candidates} == {
        "relkey:REL-A",
        "relkey:REL-B",
    }
    assert all(candidate["status"] == "AMBIGUOUS" for candidate in candidates)
    assert len(candidates) == 2


@pytest.mark.parametrize(
    ("visit_doctor", "doctor_room_map", "reason_fragment"),
    [
        ("", {"DOC1": 1}, "CCDOC is blank"),
        ("DOCX", {"DOC1": 1}, "does not match an active room filter"),
        ("DOC1", {}, "does not match an active room filter"),
    ],
)
def test_blank_unmatched_or_unmapped_visit_doctor_stays_read_only_unmatched(
    tmp_path,
    visit_doctor,
    doctor_room_map,
    reason_fragment,
):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map=doctor_room_map,
    )
    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [
            {
                "NUM": "100001",
                "CCDOC": "DOC1",
                "RELKEY": "REL-001",
                "SYS_2015": "SYS-001",
            }
        ],
    )
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))

    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "SYS-001",
                "NUM": "100001",
                "CCDOC": visit_doctor,
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090000",
            }
        ],
    )
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()

    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()

    assert payload["rooms"]["1"]["candidates"] == []
    assert payload["rooms"]["2"]["candidates"] == []
    assert len(payload["unmatched"]["candidates"]) == 1
    assert payload["unmatched"]["candidates"][0]["doctor_code"] == visit_doctor
    assert reason_fragment in payload["diagnostics"][0]["reason"]


def test_historical_invalid_and_unknown_session_visit_rows_stay_diagnostic_only(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [("REL-001", "100001", "DOC1", "1510070001")],
    )
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))

    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090000",
            },
            {
                "SYS_2015": "",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "9",
                "STIME": "090001",
            },
            {
                "SYS_2015": "",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today() - timedelta(days=1),
                "TIME_KIND": "1",
                "STIME": "090002",
            },
            {
                "SYS_2015": "",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": "20261340",
                "TIME_KIND": "1",
                "STIME": "090003",
            },
        ],
    )
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()

    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()

    assert len(payload["rooms"]["1"]["candidates"]) == 1
    assert [diagnostic["record_number"] for diagnostic in payload["diagnostics"]] == [2, 3, 4]
    reasons = {diagnostic["record_number"]: diagnostic["reason"] for diagnostic in payload["diagnostics"]}
    assert reasons[2] == "TIME_KIND is missing or unsupported"
    assert reasons[3] == "VISIT SDATE is not the current clinic date"
    assert reasons[4] == "VISIT SDATE is missing or invalid"


def test_visit_monitor_follows_browser_local_session_filter_for_all_three_sessions(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [
            {
                "NUM": "100001",
                "CCDOC": "DOC1",
                "TIME_KIND": "1",
                "RELKEY": "REL-001",
                "SYS_2015": "SYS-001",
            },
            {
                "NUM": "100002",
                "CCDOC": "DOC1",
                "TIME_KIND": "2",
                "RELKEY": "REL-002",
                "SYS_2015": "SYS-002",
            },
            {
                "NUM": "100003",
                "CCDOC": "DOC1",
                "TIME_KIND": "3",
                "RELKEY": "REL-003",
                "SYS_2015": "SYS-003",
            },
        ],
    )
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))

    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": f"SYS-{session:03d}",
                "NUM": f"10000{session}",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": str(session),
                "STIME": f"09000{session}",
            }
            for session in (1, 2, 3)
        ],
    )
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()

    morning = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    afternoon = asyncio.run(
        get_json(app, "/api/visit-monitor", cookies={"clinic_session": "2"})
    ).json()
    evening = asyncio.run(
        get_json(app, "/api/visit-monitor", cookies={"clinic_session": "3"})
    ).json()

    assert morning["selected_time_kind"] == 1
    assert [candidate["patient_no"] for candidate in morning["rooms"]["1"]["candidates"]] == [
        "100001"
    ]
    assert afternoon["selected_time_kind"] == 2
    assert [candidate["patient_no"] for candidate in afternoon["rooms"]["1"]["candidates"]] == [
        "100002"
    ]
    assert evening["selected_time_kind"] == 3
    assert [candidate["patient_no"] for candidate in evening["rooms"]["1"]["candidates"]] == [
        "100003"
    ]


def test_later_same_num_encounter_remains_eligible_when_older_encounter_is_completed(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [
            {
                "NUM": "100001",
                "CCDOC": "DOC1",
                "RELKEY": "REL-CLOSED",
                "SYS_2015": "SYS-CLOSED",
                "OVER": "T",
            },
            {
                "NUM": "100001",
                "CCDOC": "DOC1",
                "RELKEY": "REL-LATER",
                "SYS_2015": "SYS-LATER",
            },
        ],
    )
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))

    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "SYS-CLOSED",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090000",
            },
            {
                "SYS_2015": "SYS-LATER",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090001",
            },
        ],
    )
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()

    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()

    assert [candidate["encounter_key"] for candidate in payload["rooms"]["1"]["candidates"]] == [
        "relkey:REL-LATER"
    ]
    assert payload["rooms"]["1"]["candidates"][0]["status"] == "UNKNOWN"
    assert payload["diagnostics"] == [
        {
            "record_number": 1,
            "reason": "associated encounter is already HIS-derived COMPLETED",
        }
    ]


def test_visit_prefers_exact_sys2015_association_over_composite_fallback(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    today = date.today()
    write_dbf(
        his_path / "RG011M1.DBF",
        [
            ("NUM", "C", 6),
            ("TETDAY", "D", 8),
            ("CCDATE", "D", 8),
            ("CCTIME", "C", 4),
            ("CCDOC", "C", 6),
            ("TIME_KIND", "C", 1),
            ("SYS_2015", "C", 12),
            ("OVER", "C", 1),
            ("TREAT", "C", 1),
            ("GINO1", "C", 10),
            ("RELKEY", "C", 12),
        ],
        [
            {
                "NUM": "100001",
                "TETDAY": today,
                "CCDATE": today,
                "CCTIME": "0900",
                "CCDOC": "DOC1",
                "TIME_KIND": "1",
                "SYS_2015": "SYS-A",
                "TREAT": "N",
                "GINO1": "1510070001",
                "RELKEY": "RG-A",
            },
            {
                "NUM": "100001",
                "TETDAY": today,
                "CCDATE": today,
                "CCTIME": "0910",
                "CCDOC": "DOC1",
                "TIME_KIND": "1",
                "SYS_2015": "SYS-B",
                "TREAT": "N",
                "GINO1": "1510070002",
                "RELKEY": "RG-B",
            },
        ],
    )
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))
    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "SYS-B",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": today,
                "TIME_KIND": "1",
                "STIME": "090000",
            }
        ],
    )

    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()
    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()

    candidate = payload["rooms"]["1"]["candidates"][0]
    assert candidate["encounter_key"] == "relkey:RG-B"
    assert candidate["evidence_reason"] == "exact SYS_2015 association"


def test_visit_num_alone_does_not_associate_a_candidate(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(his_path / "RG011M1.DBF", [("RG-001", "100001", "DOC1", "1510070001")])
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))
    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "",
                "NUM": "100001",
                "CCDOC": "",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090000",
            }
        ],
    )

    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()
    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()

    assert payload["rooms"]["1"]["candidates"] == []
    assert payload["diagnostics"] == [
        {
            "record_number": 1,
            "reason": "NUM, CCDOC, date, and TIME_KIND are required for fallback",
        }
    ]


def test_changing_visit_bytes_reset_stability_until_two_identical_reads(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(his_path / "RG011M1.DBF", [("RG-001", "100001", "DOC1", "1510070001")])
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "080000",
            }
        ],
    )
    app = create_app(load_config(config_path))

    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090000",
            }
        ],
    )
    app.state.visit_monitor.poll_once()
    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090001",
            }
        ],
    )
    app.state.visit_monitor.poll_once()
    pending_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert pending_payload["rooms"]["1"]["candidates"] == []
    assert pending_payload["pending"][0]["consecutive_reads"] == 1

    app.state.visit_monitor.poll_once()
    accepted_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert accepted_payload["rooms"]["1"]["candidates"][0]["status"] == "UNKNOWN"
