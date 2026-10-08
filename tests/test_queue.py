import asyncio
import json
from dataclasses import replace
from datetime import date

import httpx

from clinic_queue.app import create_app
from clinic_queue.config import load_config
from clinic_queue.his import EncounterSnapshot
from clinic_queue.state import HISState, PresenceState


def snapshot(
    key: str,
    doctor: str,
    *,
    presence: PresenceState = PresenceState.PRESENT,
    state: HISState = HISState.WAITING,
    patient_no: str = "100001",
    queue_number: str = "53",
    time_kind: str | None = "1",
) -> EncounterSnapshot:
    raw_his = {"TREAT": "N", "OVER": ""}
    if time_kind is not None:
        raw_his["TIME_KIND"] = time_kind
    return EncounterSnapshot(
        encounter_key=key,
        recno=sum((index + 1) * ord(character) for index, character in enumerate(key)),
        patient_no=patient_no,
        patient_name=f"Name {patient_no}",
        doctor_code=doctor,
        visit_date=date.today(),
        registration_time="0900",
        gino1_raw="1510070053",
        queue_number=queue_number,
        his_state=state,
        initial_presence=presence,
        active=True,
        raw_his=raw_his,
    )


def create_config(tmp_path, doctor_room_map=None):
    his_path = tmp_path / "his"
    his_path.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "his_data_path": str(his_path),
                "doctor_room_map": doctor_room_map or {},
                "patient_name_field": "",
                "sqlite_path": "state/queue.sqlite3",
                "log_path": "logs/app.log",
            }
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


def test_reconcile_routes_by_ccdoc_and_keeps_unmatched_visible(tmp_path):
    from clinic_queue.queue import QueueStore

    config = create_config(tmp_path, {"DOC1": 1, "DOC2": 2})
    store = QueueStore(config.sqlite_path)
    store.reconcile(
        [
            snapshot("one", "DOC1", patient_no="100001", queue_number="15"),
            snapshot("two", "DOC2", patient_no="100002", queue_number="15"),
            snapshot("unknown", "DOCX", patient_no="100003", queue_number="16"),
        ],
        config.doctor_room_map,
    )

    board = store.get_board()
    assert [entry["encounter_key"] for entry in board["rooms"][1]["waiting"]] == ["one"]
    assert [entry["encounter_key"] for entry in board["rooms"][2]["waiting"]] == ["two"]
    assert [entry["encounter_key"] for entry in board["unmatched"]["waiting"]] == ["unknown"]


def test_present_encounters_append_per_room_and_away_stays_out_of_waiting_order(tmp_path):
    from clinic_queue.queue import QueueStore

    config = create_config(tmp_path, {"DOC1": 1, "DOC2": 2})
    store = QueueStore(config.sqlite_path)
    store.reconcile(
        [
            snapshot("r1-first", "DOC1", patient_no="100001", queue_number="15"),
            snapshot("r2-first", "DOC2", patient_no="100002", queue_number="15"),
            snapshot("r1-away", "DOC1", presence=PresenceState.AWAY, patient_no="100003"),
            snapshot("r1-second", "DOC1", patient_no="100004"),
        ],
        config.doctor_room_map,
    )

    board = store.get_board()
    assert [entry["encounter_key"] for entry in board["rooms"][1]["waiting"]] == [
        "r1-first",
        "r1-second",
    ]
    assert [entry["encounter_key"] for entry in board["rooms"][1]["away"]] == ["r1-away"]
    assert [entry["encounter_key"] for entry in board["rooms"][2]["waiting"]] == ["r2-first"]
    assert [entry["queue_position"] for entry in board["rooms"][1]["waiting"]] == [1, 2]


def test_changing_shared_filter_moves_ccdoc_matches_and_preserves_local_state(tmp_path):
    from clinic_queue.queue import QueueStore

    config = create_config(tmp_path, {"DOC1": 1, "DOC2": 2})
    store = QueueStore(config.sqlite_path)
    store.initialize_room_doctor_codes(config.doctor_room_map)
    store.reconcile(
        [
            snapshot("first", "DOC1", patient_no="100001"),
            snapshot("new-match", "DOC1", patient_no="100002"),
            snapshot("other-room", "DOC2", patient_no="100003"),
        ],
        config.doctor_room_map,
    )
    store.set_overdue("new-match", True)
    store.reconcile(
        [
            snapshot("first", "DOC1", patient_no="100001"),
            snapshot("new-match", "DOCX", patient_no="100002"),
            snapshot("other-room", "DOC2", patient_no="100003"),
        ],
        config.doctor_room_map,
    )
    store.set_room_doctor_code(1, "DOCX")

    board = store.get_board()
    assert [entry["encounter_key"] for entry in board["rooms"][1]["waiting"]] == ["new-match"]
    assert board["rooms"][1]["waiting"][0]["overdue"] is True
    assert [entry["encounter_key"] for entry in board["unmatched"]["waiting"]] == ["first"]
    assert [entry["encounter_key"] for entry in board["rooms"][2]["waiting"]] == ["other-room"]


def test_encounter_identity_survives_physical_record_number_changes(tmp_path):
    from clinic_queue.queue import QueueStore

    config = create_config(tmp_path, {"DOC1": 1})
    store = QueueStore(config.sqlite_path)
    first = replace(snapshot("first", "DOC1", patient_no="100001"), recno=1)
    second = replace(snapshot("second", "DOC1", patient_no="100002"), recno=2)
    store.reconcile([first, second], config.doctor_room_map)
    store.set_presence("first", PresenceState.AWAY)

    store.reconcile(
        [replace(first, recno=2), replace(second, recno=1)],
        config.doctor_room_map,
    )

    board = store.get_board()
    assert [entry["encounter_key"] for entry in board["rooms"][1]["away"]] == ["first"]
    assert [entry["encounter_key"] for entry in board["rooms"][1]["waiting"]] == ["second"]
    assert board["invalidated"] == []


def test_page_renders_named_rooms_and_read_only_unmatched_records(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    app = create_app(config)
    app.state.queue_store.reconcile(
        [snapshot("unknown-key", "DOCX", patient_no="100001")],
        config.doctor_room_map,
    )

    async def fetch_home_page():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/")

    response = asyncio.run(fetch_home_page())

    assert response.status_code == 200
    assert 'aria-label="一診"' in response.text
    assert 'aria-label="二診"' in response.text
    assert "未納入一診／二診篩選" in response.text
    assert "unknown-key" in response.text
    assert "指派" not in response.text
    assert 'data-assign-room' not in response.text


def test_manual_room_assignment_api_is_absent(tmp_path):
    config = create_config(tmp_path, {})
    app = create_app(config)
    app.state.queue_store.reconcile([snapshot("unknown-key", "DOCX")], config.doctor_room_map)

    async def assign():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/assign",
                json={"encounter_key": "unknown-key", "room_id": 2},
            )

    response = asyncio.run(assign())

    assert response.status_code == 404
    assert app.state.queue_store.get_board()["unmatched"]["waiting"][0]["encounter_key"] == "unknown-key"
