import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
import os
import re
from threading import Event, Lock

import httpx
import pytest

from clinic_queue.app import create_app
from clinic_queue.config import load_config
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


def assert_visit_surface_is_read_only(payload, page_html):
    serialized_payload = json.dumps(payload, ensure_ascii=False).upper()
    assert "ACTIVE" not in serialized_payload
    assert '"ACTION"' not in serialized_payload

    monitor_sections = re.findall(
        r'<section class="visit-monitor"[^>]*>.*?</section>',
        page_html,
        flags=re.DOTALL,
    )
    assert monitor_sections
    for section in monitor_sections:
        assert "data-action=" not in section
        assert "/api/" not in section
        assert not re.search(r"<(?:button|form|input|select)\b", section)


def test_visit_monitor_api_and_ui_never_emit_active_or_mutation_controls(tmp_path):
    app, _ = _make_candidate_app(tmp_path)

    api_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    page_response = asyncio.run(get_json(app, "/"))

    assert api_payload["status"] == "UNKNOWN"
    assert_visit_surface_is_read_only(api_payload, page_response.text)


def test_visit_monitor_is_always_on_and_projects_a_stable_startup_card(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=False,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(his_path / "RG011M1.DBF", [("RG-001", "100001", "DOC1", "1510070001")])
    write_visit_fixture(
        his_path / "RG011M1_VISIT.DBF",
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

    app = create_app(load_config(config_path))
    payload = asyncio.run(get_json(app, "/api/queue")).json()
    page = asyncio.run(get_json(app, "/")).text

    monitor = payload["visit_monitor"]
    assert "enabled" not in monitor
    assert monitor["status"] == "UNKNOWN"
    assert monitor["rooms"]["1"]["status"] == "UNKNOWN"
    assert monitor["rooms"]["1"]["candidates"][0]["patient_no"] == "100001"
    assert "看診中" in page
    assert "看診中（實驗）" not in page


def test_unique_current_patient_suppresses_all_matching_waiting_cards_only_in_scope(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=False,
        doctor_room_map={"DOC1": 1, "DOC2": 2},
    )
    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [
            {
                "NUM": "100001",
                "CCDOC": "DOC1",
                "RELKEY": "RG-001",
                "SYS_2015": "SYS-001",
            },
            {
                "NUM": "100001",
                "CCDOC": "DOC1",
                "RELKEY": "RG-002",
                "SYS_2015": "SYS-002",
            },
            {
                "NUM": "100002",
                "CCDOC": "DOC1",
                "RELKEY": "RG-003",
                "SYS_2015": "SYS-003",
            },
            {
                "NUM": "100001",
                "CCDOC": "DOC2",
                "RELKEY": "RG-004",
                "SYS_2015": "SYS-004",
            },
        ],
    )
    write_visit_fixture(
        his_path / "RG011M1_VISIT.DBF",
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

    app = create_app(load_config(config_path))
    payload = asyncio.run(get_json(app, "/api/queue")).json()
    waiting = payload["rooms"]["1"]["waiting"]
    raw_waiting = app.state.queue_store.get_board(1)["rooms"][1]["waiting"]

    assert [entry["patient_no"] for entry in waiting] == ["100002"]
    assert [entry["queue_position"] for entry in waiting] == [3]
    assert [entry["display_position"] for entry in waiting] == [1]
    assert {entry["encounter_key"] for entry in raw_waiting} == {
        "relkey:RG-001",
        "relkey:RG-002",
        "relkey:RG-003",
    }
    assert [entry["queue_position"] for entry in raw_waiting] == [1, 2, 3]
    assert set(payload["visit_monitor"]["suppressed_encounter_keys"]) == {
        "relkey:RG-001",
        "relkey:RG-002",
    }
    assert payload["rooms"]["2"]["waiting"][0]["patient_no"] == "100001"


def test_suppression_follows_stale_ambiguity_switch_and_deletion_without_reordering(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=False,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [
            {"NUM": "100001", "CCDOC": "DOC1", "RELKEY": "RG-001", "SYS_2015": "SYS-001"},
            {"NUM": "100002", "CCDOC": "DOC1", "RELKEY": "RG-002", "SYS_2015": "SYS-002"},
        ],
    )
    visit_path = his_path / "RG011M1_VISIT.DBF"
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
            }
        ],
    )
    app = create_app(load_config(config_path))

    initial = asyncio.run(get_json(app, "/api/queue")).json()
    assert [entry["encounter_key"] for entry in initial["rooms"]["1"]["waiting"]] == [
        "relkey:RG-002"
    ]
    initial_positions = {
        entry["encounter_key"]: entry["queue_position"]
        for entry in app.state.queue_store.get_board(1)["rooms"][1]["waiting"]
    }
    assert initial["visit_monitor"]["suppressed_encounter_keys"] == ["relkey:RG-001"]

    visit_path.unlink()
    app.state.visit_monitor.poll_once()
    stale = asyncio.run(get_json(app, "/api/queue")).json()
    assert stale["visit_monitor"]["status"] == "STALE"
    assert stale["visit_monitor"]["suppressed_encounter_keys"] == ["relkey:RG-001"]
    assert [entry["encounter_key"] for entry in stale["rooms"]["1"]["waiting"]] == [
        "relkey:RG-002"
    ]

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
                "SYS_2015": "SYS-002",
                "NUM": "100002",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090001",
            },
        ],
    )
    app.state.visit_monitor.poll_once()
    ambiguous_pending = asyncio.run(get_json(app, "/api/queue")).json()
    assert ambiguous_pending["visit_monitor"]["status"] == "STALE"
    app.state.visit_monitor.poll_once()
    ambiguous = asyncio.run(get_json(app, "/api/queue")).json()
    assert ambiguous["visit_monitor"]["status"] == "AMBIGUOUS"
    assert ambiguous["visit_monitor"]["suppressed_encounter_keys"] == []
    assert [entry["encounter_key"] for entry in ambiguous["rooms"]["1"]["waiting"]] == [
        "relkey:RG-001",
        "relkey:RG-002",
    ]

    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "SYS-002",
                "NUM": "100002",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090001",
            }
        ],
    )
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()
    switched = asyncio.run(get_json(app, "/api/queue")).json()
    assert switched["visit_monitor"]["status"] == "UNKNOWN"
    assert switched["visit_monitor"]["suppressed_encounter_keys"] == ["relkey:RG-002"]
    assert [entry["encounter_key"] for entry in switched["rooms"]["1"]["waiting"]] == [
        "relkey:RG-001"
    ]

    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "SYS-002",
                "NUM": "100002",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090001",
                "_DELETED": True,
            }
        ],
    )
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()
    deleted = asyncio.run(get_json(app, "/api/queue")).json()
    assert deleted["visit_monitor"]["suppressed_encounter_keys"] == []
    assert [entry["encounter_key"] for entry in deleted["rooms"]["1"]["waiting"]] == [
        "relkey:RG-001",
        "relkey:RG-002",
    ]
    assert {
        entry["encounter_key"]: entry["queue_position"]
        for entry in app.state.queue_store.get_board(1)["rooms"][1]["waiting"]
    } == initial_positions


def test_suppressed_waiting_encounter_moves_away_returns_and_completion_stays_his_owned(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=False,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [
            {"NUM": "100001", "CCDOC": "DOC1", "RELKEY": "RG-001", "SYS_2015": "SYS-001"},
            {"NUM": "100002", "CCDOC": "DOC1", "RELKEY": "RG-002", "SYS_2015": "SYS-002"},
        ],
    )
    visit_path = his_path / "RG011M1_VISIT.DBF"
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
            }
        ],
    )
    app = create_app(load_config(config_path))

    async def change_presence(action):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/action",
                json={"encounter_key": "relkey:RG-001", "action": action},
            )

    away = asyncio.run(change_presence("away"))
    assert away.status_code == 200
    away_payload = away.json()
    assert [entry["encounter_key"] for entry in away_payload["rooms"]["1"]["away"]] == [
        "relkey:RG-001"
    ]
    assert away_payload["visit_monitor"]["suppressed_encounter_keys"] == []

    present = asyncio.run(change_presence("present"))
    assert present.status_code == 200
    present_payload = present.json()
    assert [entry["encounter_key"] for entry in present_payload["rooms"]["1"]["waiting"]] == [
        "relkey:RG-002"
    ]
    assert present_payload["visit_monitor"]["suppressed_encounter_keys"] == ["relkey:RG-001"]

    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [
            {"NUM": "100001", "CCDOC": "DOC1", "RELKEY": "RG-001", "SYS_2015": "SYS-001", "OVER": "T"},
            {"NUM": "100002", "CCDOC": "DOC1", "RELKEY": "RG-002", "SYS_2015": "SYS-002"},
        ],
    )
    app.state.his_poller.poll_once()
    completed = asyncio.run(get_json(app, "/api/queue")).json()
    assert completed["visit_monitor"]["suppressed_encounter_keys"] == []
    assert "relkey:RG-001" not in {
        entry["encounter_key"] for entry in completed["rooms"]["1"]["waiting"]
    }
    assert "relkey:RG-001" in {
        entry["encounter_key"] for entry in completed["completed"]
    }


def test_queue_page_and_all_queue_mutations_share_the_filtered_visit_projection(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=False,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [
            {"NUM": "100001", "CCDOC": "DOC1", "RELKEY": "RG-001", "SYS_2015": "SYS-001"},
            {"NUM": "100002", "CCDOC": "DOC1", "RELKEY": "RG-002", "SYS_2015": "SYS-002"},
        ],
    )
    write_visit_fixture(
        his_path / "RG011M1_VISIT.DBF",
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
    app = create_app(load_config(config_path))

    async def exercise_endpoints():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await asyncio.gather(
                client.get("/api/queue"),
                client.get("/api/visit-monitor"),
                client.get("/"),
                client.post(
                    "/api/action",
                    json={
                        "encounter_key": "relkey:RG-002",
                        "action": "overdue",
                        "value": True,
                    },
                ),
                client.post(
                    "/api/reorder",
                    json={"encounter_key": "relkey:RG-002", "position": 1},
                ),
                client.post(
                    "/api/room-doctor-code",
                    json={"room_id": 1, "doctor_code": "DOC1"},
                ),
            )

    queue, monitor, page, action, reorder, room_code = asyncio.run(exercise_endpoints())
    assert all(response.status_code == 200 for response in (queue, monitor, page, action, reorder, room_code))

    queue_payload = queue.json()
    expected_keys = ["relkey:RG-001"]
    assert [entry["encounter_key"] for entry in queue_payload["rooms"]["1"]["waiting"]] == [
        "relkey:RG-002"
    ]
    assert monitor.json()["suppressed_encounter_keys"] == ["relkey:RG-001"]
    for response in (action, reorder, room_code):
        payload = response.json()
        assert payload["visit_monitor"]["suppressed_encounter_keys"] == expected_keys
        assert [entry["encounter_key"] for entry in payload["rooms"]["1"]["waiting"]] == [
            "relkey:RG-002"
        ]
    assert 'data-encounter-key="relkey:RG-001"' not in page.text
    assert 'data-visit-encounter-key="relkey:RG-001"' in page.text


def test_visit_source_failure_preserves_ordinary_queue_and_rendered_patients(tmp_path):
    app, visit_path = _make_candidate_app(tmp_path)
    before = asyncio.run(get_json(app, "/api/queue")).json()

    visit_path.unlink()
    app.state.visit_monitor.poll_once()

    after = asyncio.run(get_json(app, "/api/queue")).json()
    page_response = asyncio.run(get_json(app, "/"))

    queue_keys = {
        "selected_time_kind",
        "room_doctor_codes",
        "rooms",
        "unmatched",
        "completed",
        "invalidated",
        "unconfirmed",
    }
    assert {key: after[key] for key in queue_keys} == {
        key: before[key] for key in queue_keys
    }
    assert after["visit_monitor"]["status"] == "STALE"
    assert "#100001" in page_response.text
    assert after["rooms"]["1"]["waiting"] == []
    assert app.state.queue_store.get_board(1)["rooms"][1]["waiting"][0]["patient_no"] == "100001"
    assert 'data-visit-encounter-key="relkey:RG-001"' in page_response.text
    assert "VISIT 資料來源暫時失效" in page_response.text
    assert_visit_surface_is_read_only(after["visit_monitor"], page_response.text)


def test_initial_visit_error_keeps_ordinary_queue_visible_and_unchanged(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(his_path / "RG011M1.DBF", [("RG-001", "100001", "DOC1", "1510070001")])
    app = create_app(load_config(config_path))

    before = asyncio.run(get_json(app, "/api/queue")).json()
    monitor_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    after = asyncio.run(get_json(app, "/api/queue")).json()
    page_response = asyncio.run(get_json(app, "/"))

    queue_keys = {
        "selected_time_kind",
        "room_doctor_codes",
        "rooms",
        "unmatched",
        "completed",
        "invalidated",
        "unconfirmed",
    }
    assert {key: after[key] for key in queue_keys} == {
        key: before[key] for key in queue_keys
    }
    assert monitor_payload["status"] == "ERROR"
    assert after["rooms"]["1"]["waiting"][0]["patient_no"] == "100001"
    assert 'data-encounter-key="relkey:RG-001"' in page_response.text
    assert "VISIT 資料來源錯誤" in page_response.text
    assert_visit_surface_is_read_only(monitor_payload, page_response.text)


def test_monitor_never_mutates_visit_bytes_during_deletion_and_recovery(tmp_path):
    app, visit_path = _make_candidate_app(tmp_path)

    _write_candidate_visit(visit_path, deleted=True)
    deleted_bytes_before_poll = visit_path.read_bytes()
    app.state.visit_monitor.poll_once()
    assert visit_path.read_bytes() == deleted_bytes_before_poll
    app.state.visit_monitor.poll_once()
    deleted_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert deleted_payload["rooms"]["1"]["candidates"] == []
    assert visit_path.read_bytes() == deleted_bytes_before_poll

    _write_candidate_visit(visit_path, deleted=False, stime="090001")
    restored_bytes_before_poll = visit_path.read_bytes()
    app.state.visit_monitor.poll_once()
    assert visit_path.read_bytes() == restored_bytes_before_poll
    app.state.visit_monitor.poll_once()
    restored_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert restored_payload["rooms"]["1"]["candidates"]
    assert visit_path.read_bytes() == restored_bytes_before_poll


def test_duplicate_composite_association_stays_diagnostic_without_a_candidate(tmp_path):
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
                "SYS_2015": "",
            },
            {
                "NUM": "100001",
                "CCDOC": "DOC1",
                "RELKEY": "REL-B",
                "SYS_2015": "",
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
    app.state.visit_monitor.poll_once()

    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()

    assert payload["status"] == "NONE"
    assert payload["rooms"]["1"]["candidates"] == []
    assert payload["diagnostics"] == [
        {
            "record_number": 1,
            "reason": "composite encounter association is ambiguous",
        }
    ]


def test_visit_candidates_use_the_matching_room_for_multiple_physicians(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1, "DOC2": 2},
    )
    write_rg_fixture(
        his_path / "RG011M1.DBF",
        [
            {
                "NUM": "100001",
                "CCDOC": "DOC1",
                "RELKEY": "REL-1",
                "SYS_2015": "SYS-1",
            },
            {
                "NUM": "100002",
                "CCDOC": "DOC2",
                "RELKEY": "REL-2",
                "SYS_2015": "SYS-2",
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
                "SYS_2015": "SYS-1",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090000",
            },
            {
                "SYS_2015": "SYS-2",
                "NUM": "100002",
                "CCDOC": "DOC2",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090001",
            },
        ],
    )
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()

    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()

    assert [candidate["patient_no"] for candidate in payload["rooms"]["1"]["candidates"]] == [
        "100001"
    ]
    assert [candidate["patient_no"] for candidate in payload["rooms"]["2"]["candidates"]] == [
        "100002"
    ]
    assert payload["unmatched"]["candidates"] == []


def test_visit_without_a_unique_current_encounter_stays_diagnostic(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(his_path / "RG011M1.DBF", [("REL-001", "100001", "DOC1", "1510070001")])
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))

    write_visit_fixture(
        visit_path,
        [
            {
                "SYS_2015": "",
                "NUM": "100002",
                "CCDOC": "DOC1",
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
            "reason": "no unique NUM+CCDOC+date+TIME_KIND encounter match",
        }
    ]


def test_visit_monitor_is_always_on_and_uses_configurable_source(tmp_path):
    config_path, _ = write_config(tmp_path)

    config = load_config(config_path)

    assert config.rg011m1_visit_filename == "RG011M1_VISIT.DBF"
    assert config.visit_filename == "RG011M1_VISIT.DBF"


@pytest.mark.parametrize("value", [None, "true", 1, []])
def test_legacy_visit_monitor_setting_is_ignored(tmp_path, value):
    config_path, _ = write_config(tmp_path)
    settings = json.loads(config_path.read_text(encoding="utf-8"))
    if value is None:
        settings["visit_monitor_enabled"] = None
    else:
        settings["visit_monitor_enabled"] = value
    config_path.write_text(json.dumps(settings), encoding="utf-8")

    load_config(config_path)


def test_legacy_disabled_setting_does_not_disable_or_hide_visit_source(tmp_path):
    config_path, his_path = write_config(tmp_path)
    write_rg_fixture(his_path / "RG011M1.DBF", [])
    config = load_config(config_path)
    app = create_app(config)

    api_response = asyncio.run(get_json(app, "/api/visit-monitor"))
    page_response = asyncio.run(get_json(app, "/"))

    assert api_response.status_code == 200
    payload = api_response.json()
    assert "enabled" not in payload
    assert payload["status"] == "ERROR"
    assert payload["source_health"]["status"] == "ERROR"
    assert payload["source_health"]["baseline_established"] is False
    assert payload["source_health"]["record_count"] == 0
    assert payload["source_health"]["error"]
    assert payload["baseline"] == {"established": False, "record_count": 0}
    assert all(room["status"] == "ERROR" for room in payload["rooms"].values())
    assert payload["unmatched"] == {"status": "ERROR", "candidates": []}
    assert page_response.status_code == 200
    assert "看診中" in page_response.text
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
    assert "enabled" not in payload
    assert payload["status"] == "UNKNOWN"
    assert payload["source_health"]["status"] == "OK"
    assert payload["source_health"]["baseline_established"] is True
    assert payload["source_health"]["record_count"] == 1
    assert payload["source_health"]["filename"] == "CONTROLLED_VISIT.DBF"
    assert payload["baseline"] == {"established": True, "record_count": 1}
    assert payload["rooms"]["1"]["status"] == "UNKNOWN"
    assert payload["rooms"]["1"]["candidates"][0]["patient_no"] == "100001"
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
    assert "enabled" not in payload
    assert payload["status"] == "ERROR"
    assert payload["source_health"]["status"] == "ERROR"
    assert payload["source_health"]["baseline_established"] is False
    assert payload["source_health"]["error"]
    assert all(room["status"] == "ERROR" for room in payload["rooms"].values())
    assert payload["unmatched"] == {"status": "ERROR", "candidates": []}
    assert page_response.status_code == 200
    assert "看診中" in page_response.text
    assert "看診中（實驗）" not in page_response.text
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
            "visit_date": date.today().isoformat(),
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
            "visit_date": date.today().isoformat(),
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


def test_visit_unmatched_and_diagnostics_render_in_separate_read_only_section(tmp_path):
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
                "RELKEY": "REL-U",
                "SYS_2015": "SYS-U",
            },
            {
                "NUM": "100002",
                "CCDOC": "DOC1",
                "RELKEY": "REL-R",
                "SYS_2015": "SYS-R",
            },
            {
                "NUM": "100003",
                "CCDOC": "DOC1",
                "RELKEY": "REL-C",
                "SYS_2015": "SYS-C",
                "OVER": "T",
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
                "SYS_2015": "SYS-U",
                "NUM": "100001",
                "CCDOC": "DOCX",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090000",
            },
            {
                "SYS_2015": "",
                "NUM": "100999",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090001",
            },
            {
                "SYS_2015": "SYS-C",
                "NUM": "100003",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090002",
            },
            {
                "SYS_2015": "SYS-U",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090003",
                "_DELETED": True,
            },
        ],
    )
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()

    page_response = asyncio.run(get_json(app, "/"))
    visit_diagnostics = re.search(
        r'<details class="visit-monitor-diagnostics"[^>]*>.*?</details>',
        page_response.text,
        flags=re.DOTALL,
    )
    ordinary_unmatched = re.search(
        r'<section class="unmatched"[^>]*>.*?</section>',
        page_response.text,
        flags=re.DOTALL,
    )

    assert page_response.status_code == 200
    assert visit_diagnostics is not None
    assert "#100001" in visit_diagnostics.group(0)
    assert "no unique NUM+CCDOC+date+TIME_KIND encounter match" in visit_diagnostics.group(0)
    assert "associated encounter is already HIS-derived COMPLETED" in visit_diagnostics.group(0)
    assert "VISIT evidence is logically deleted" in visit_diagnostics.group(0)
    assert "data-action=" not in visit_diagnostics.group(0)
    assert not re.search(r"<(?:button|form|input|select)\b", visit_diagnostics.group(0))
    assert ordinary_unmatched is not None
    assert "#100001" not in ordinary_unmatched.group(0)


def test_visit_monitor_concurrent_poller_and_httpx_requests_are_safe(tmp_path):
    app, _ = _make_candidate_app(tmp_path)

    monitor = app.state.visit_monitor
    original_provider = monitor._encounter_provider
    assert original_provider is not None
    provider_entered = Event()
    release_provider = Event()
    provider_calls = 0
    provider_calls_lock = Lock()

    def gated_provider():
        nonlocal provider_calls
        with provider_calls_lock:
            provider_calls += 1
            first_call = provider_calls == 1
        if first_call:
            provider_entered.set()
            if not release_provider.wait(timeout=5):
                raise AssertionError("timed out waiting to release the encounter snapshot")
        return original_provider()

    monitor._encounter_provider = gated_provider
    poll_started = Event()
    poll_finished = Event()

    def poll_once():
        poll_started.set()
        app.state.his_poller.poll_once()
        poll_finished.set()

    async def request_once():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/visit-monitor")

    with ThreadPoolExecutor(max_workers=2) as executor:
        request_future = executor.submit(lambda: asyncio.run(request_once()))
        assert provider_entered.wait(timeout=5)
        poll_future = executor.submit(poll_once)
        assert poll_started.wait(timeout=5)
        assert not poll_finished.wait(timeout=0.2)
        release_provider.set()
        response = request_future.result()
        poll_future.result()

    assert response.status_code == 200
    assert response.json()["rooms"]["1"]["candidates"]


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
    assert pending_payload["rooms"]["1"]["candidates"][0]["status"] == "UNKNOWN"
    assert pending_payload["pending"][0]["consecutive_reads"] == 1

    app.state.visit_monitor.poll_once()
    accepted_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert accepted_payload["rooms"]["1"]["candidates"][0]["status"] == "UNKNOWN"


@pytest.mark.parametrize("interruption", ["incomplete", "failed"])
def test_interrupted_visit_stability_requires_two_new_complete_reads(tmp_path, interruption):
    app, visit_path = _make_candidate_app(tmp_path)

    _write_candidate_visit(visit_path, stime="090001")
    app.state.visit_monitor.poll_once()

    changed_bytes = visit_path.read_bytes()
    if interruption == "incomplete":
        header_length = int.from_bytes(changed_bytes[8:10], "little")
        visit_path.write_bytes(changed_bytes[: header_length + 3])
    else:
        visit_path.write_bytes(b"")
    app.state.visit_monitor.poll_once()

    interrupted_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert interrupted_payload["status"] == "STALE"
    assert interrupted_payload["rooms"]["1"]["candidates"]
    assert all(item["consecutive_reads"] == 0 for item in interrupted_payload["pending"])

    visit_path.write_bytes(changed_bytes)
    app.state.visit_monitor.poll_once()
    recovered_once = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert recovered_once["source_health"]["status"] == "OK"
    assert recovered_once["pending"] == [
        {
            "record_number": 1,
            "consecutive_reads": 1,
            "reason": "awaiting a second identical complete read",
        }
    ]

    app.state.visit_monitor.poll_once()
    recovered_twice = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert recovered_twice["rooms"]["1"]["candidates"]


def _write_candidate_visit(path, *, deleted=False, stime="090000"):
    write_visit_fixture(
        path,
        [
            {
                "SYS_2015": "",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": stime,
                "_DELETED": deleted,
            }
        ],
    )


def _make_candidate_app(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(his_path / "RG011M1.DBF", [("RG-001", "100001", "DOC1", "1510070001")])
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))
    _write_candidate_visit(visit_path)
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()
    return app, visit_path


def test_his_poller_poll_once_drives_visit_append_deletion_failure_and_recovery(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(his_path / "RG011M1.DBF", [("RG-001", "100001", "DOC1", "1510070001")])
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))
    poller = app.state.his_poller

    _write_candidate_visit(visit_path)
    poller.poll_once()
    first_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert first_payload["rooms"]["1"]["candidates"] == []
    assert first_payload["pending"][0]["record_number"] == 1

    poller.poll_once()
    appended_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert appended_payload["rooms"]["1"]["candidates"]

    _write_candidate_visit(visit_path, deleted=True)
    poller.poll_once()
    poller.poll_once()
    deleted_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert deleted_payload["rooms"]["1"]["candidates"] == []
    assert any("logically deleted" in item["reason"] for item in deleted_payload["diagnostics"])

    _write_candidate_visit(visit_path, stime="090001")
    poller.poll_once()
    poller.poll_once()
    restored_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert restored_payload["rooms"]["1"]["candidates"]

    stable_bytes = visit_path.read_bytes()
    visit_path.write_bytes(b"")
    poller.poll_once()
    stale_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert stale_payload["source_health"]["status"] == "STALE"
    assert stale_payload["rooms"]["1"]["candidates"]

    visit_path.write_bytes(stable_bytes)
    poller.poll_once()
    recovered_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert recovered_payload["source_health"]["status"] == "OK"
    assert recovered_payload["rooms"]["1"]["candidates"]


def test_his_poller_rebuilds_visit_baseline_after_layout_change_without_record_replay(tmp_path):
    app, visit_path = _make_candidate_app(tmp_path)
    replacement = visit_path.with_name("replacement.DBF")
    write_dbf(
        replacement,
        [
            ("SYS_2015", "C", 12),
            ("NUM", "C", 6),
            ("CCDOC", "C", 6),
            ("SDATE", "D", 8),
            ("TIME_KIND", "C", 1),
            ("STIME", "C", 6),
            ("EXTRA", "C", 1),
        ],
        [
            {
                "SYS_2015": "",
                "NUM": "100001",
                "CCDOC": "DOC1",
                "SDATE": date.today(),
                "TIME_KIND": "1",
                "STIME": "090001",
                "EXTRA": "X",
            }
        ],
    )
    os.replace(replacement, visit_path)

    app.state.his_poller.poll_once()
    first_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert first_payload["source_health"]["status"] == "STALE"
    assert first_payload["rooms"]["1"]["candidates"]

    app.state.his_poller.poll_once()
    second_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert second_payload["source_health"]["status"] == "OK"
    assert second_payload["baseline"]["record_count"] == 1
    assert second_payload["rooms"]["1"]["candidates"]


@pytest.mark.parametrize("interruption", ["incomplete", "failed"])
def test_replacement_baseline_restarts_after_interrupted_read(tmp_path, interruption):
    app, visit_path = _make_candidate_app(tmp_path)
    replacement = visit_path.with_name("replacement.DBF")
    write_visit_fixture(
        replacement,
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
    os.replace(replacement, visit_path)
    replacement_bytes = visit_path.read_bytes()

    app.state.visit_monitor.poll_once()
    pending_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert pending_payload["source_health"]["status"] == "STALE"
    assert pending_payload["rooms"]["1"]["candidates"]

    if interruption == "incomplete":
        header_length = int.from_bytes(replacement_bytes[8:10], "little")
        visit_path.write_bytes(replacement_bytes[: header_length + 3])
    else:
        visit_path.write_bytes(b"")
    app.state.visit_monitor.poll_once()

    visit_path.write_bytes(replacement_bytes)
    app.state.visit_monitor.poll_once()
    recovered_once = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert recovered_once["source_health"]["status"] == "STALE"
    assert recovered_once["baseline"]["record_count"] == 0
    assert recovered_once["rooms"]["1"]["candidates"]

    app.state.visit_monitor.poll_once()
    recovered_twice = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert recovered_twice["source_health"]["status"] == "OK"
    assert recovered_twice["baseline"]["record_count"] == 1
    assert recovered_twice["rooms"]["1"]["candidates"]


def test_stable_logical_deletion_removes_only_deleted_evidence(tmp_path):
    app, visit_path = _make_candidate_app(tmp_path)

    _write_candidate_visit(visit_path, deleted=True)
    app.state.visit_monitor.poll_once()
    pending_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert pending_payload["rooms"]["1"]["candidates"]

    app.state.visit_monitor.poll_once()
    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()

    assert payload["status"] == "NONE"
    assert payload["rooms"]["1"]["candidates"] == []
    assert any("logically deleted" in item["reason"] for item in payload["diagnostics"])


def test_deleting_one_duplicate_evidence_keeps_the_other_evidence(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(his_path / "RG011M1.DBF", [("RG-001", "100001", "DOC1", "1510070001")])
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))
    rows = [
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
            "TIME_KIND": "1",
            "STIME": "090001",
        },
    ]
    write_visit_fixture(visit_path, rows)
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()
    before = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert before["rooms"]["1"]["candidates"][0]["evidence_count"] == 2

    rows[0]["_DELETED"] = True
    write_visit_fixture(visit_path, rows)
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()
    after = asyncio.run(get_json(app, "/api/visit-monitor")).json()

    candidate = after["rooms"]["1"]["candidates"][0]
    assert candidate["evidence_count"] == 1
    assert candidate["record_locators"] == [
        {"source": "RG011M1_VISIT.DBF", "record_number": 2}
    ]


def test_first_visit_source_failure_is_error_without_a_fabricated_candidate(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={"DOC1": 1},
    )
    write_rg_fixture(his_path / "RG011M1.DBF", [("RG-001", "100001", "DOC1", "1510070001")])
    app = create_app(load_config(config_path))

    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()

    assert payload["status"] == "ERROR"
    assert payload["source_health"]["status"] == "ERROR"
    assert payload["rooms"]["1"]["candidates"] == []


def test_temporary_visit_source_failure_preserves_last_observation_as_stale(tmp_path):
    app, visit_path = _make_candidate_app(tmp_path)
    visit_path.unlink()

    app.state.visit_monitor.poll_once()
    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    page_response = asyncio.run(get_json(app, "/")).text

    assert payload["status"] == "STALE"
    assert payload["source_health"]["status"] == "STALE"
    assert payload["rooms"]["1"]["status"] == "STALE"
    assert payload["rooms"]["1"]["candidates"][0]["status"] == "UNKNOWN"
    assert payload["source_health"]["error"]
    room_monitor = re.search(
        r'<section class="visit-monitor"[^>]*data-visit-monitor-room="1"[^>]*>.*?</section>',
        page_response,
        flags=re.DOTALL,
    )
    assert room_monitor is not None
    assert "STALE" in room_monitor.group(0)
    assert "VISIT 資料來源暫時失效" in room_monitor.group(0)


def test_stale_visit_source_propagates_to_unmatched_monitor_status(tmp_path):
    config_path, his_path = write_config(
        tmp_path,
        enabled=True,
        doctor_room_map={},
    )
    write_rg_fixture(his_path / "RG011M1.DBF", [("RG-001", "100001", "DOC1", "1510070001")])
    visit_path = his_path / "RG011M1_VISIT.DBF"
    write_visit_fixture(visit_path, [])
    app = create_app(load_config(config_path))
    _write_candidate_visit(visit_path)
    app.state.visit_monitor.poll_once()
    app.state.visit_monitor.poll_once()

    visit_path.unlink()
    app.state.visit_monitor.poll_once()
    payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()

    assert payload["unmatched"]["status"] == "STALE"
    assert payload["unmatched"]["candidates"]
    assert payload["unmatched"]["candidates"][0]["status"] == "UNKNOWN"


def test_truncated_visit_record_stays_pending_and_recovers_without_clearing_evidence(tmp_path):
    app, visit_path = _make_candidate_app(tmp_path)
    complete_bytes = visit_path.read_bytes()
    header_length = int.from_bytes(complete_bytes[8:10], "little")
    visit_path.write_bytes(complete_bytes[: header_length + 3])

    app.state.visit_monitor.poll_once()
    stale_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert stale_payload["status"] == "STALE"
    assert stale_payload["pending"]
    assert stale_payload["rooms"]["1"]["candidates"]

    visit_path.write_bytes(complete_bytes)
    app.state.visit_monitor.poll_once()
    recovered_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert recovered_payload["source_health"]["status"] == "OK"
    assert recovered_payload["rooms"]["1"]["candidates"]


def test_replaced_visit_file_rebuilds_baseline_after_two_stable_reads(tmp_path):
    app, visit_path = _make_candidate_app(tmp_path)
    replacement = visit_path.with_name("replacement.DBF")
    write_visit_fixture(replacement, [])
    os.replace(replacement, visit_path)

    app.state.visit_monitor.poll_once()
    first_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert first_payload["status"] == "STALE"
    assert first_payload["rooms"]["1"]["candidates"]

    app.state.visit_monitor.poll_once()
    second_payload = asyncio.run(get_json(app, "/api/visit-monitor")).json()
    assert second_payload["source_health"]["status"] == "OK"
    assert second_payload["baseline"]["record_count"] == 0
    assert second_payload["rooms"]["1"]["candidates"] == []


def test_restart_rebuilds_visit_baseline_without_replaying_existing_rows(tmp_path):
    first_app, visit_path = _make_candidate_app(tmp_path)
    first_payload = asyncio.run(get_json(first_app, "/api/visit-monitor")).json()
    assert first_payload["rooms"]["1"]["candidates"]

    restarted_app = create_app(first_app.state.config)
    restarted_payload = asyncio.run(get_json(restarted_app, "/api/visit-monitor")).json()

    assert restarted_payload["status"] == "UNKNOWN"
    assert restarted_payload["baseline"]["record_count"] == 1
    assert restarted_payload["rooms"]["1"]["candidates"]
