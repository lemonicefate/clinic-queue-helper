import asyncio
import json
import logging
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


def test_reconcile_routes_by_doctor_and_unknown_doctors_to_unassigned(tmp_path, caplog):
    from clinic_queue.queue import QueueStore

    config = create_config(tmp_path, {"DOC1": 1, "DOC2": 2})
    store = QueueStore(config.sqlite_path)

    with caplog.at_level(logging.WARNING, logger="clinic_queue.queue"):
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
    assert [entry["encounter_key"] for entry in board["unassigned"]] == ["unknown"]
    assert "DOCX" in caplog.text


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


def test_staff_assignment_moves_unassigned_encounter_to_room_tail_and_survives_his_reads(tmp_path):
    from clinic_queue.queue import QueueStore

    config = create_config(tmp_path, {"DOC1": 1})
    store = QueueStore(config.sqlite_path)
    first = snapshot("first", "DOC1", patient_no="100001")
    unknown = snapshot("unknown", "DOCX", patient_no="100002")
    store.reconcile([first, unknown], config.doctor_room_map)

    store.assign_room("unknown", 1)
    store.reconcile([first, unknown], config.doctor_room_map)

    board = store.get_board()
    assert [entry["encounter_key"] for entry in board["rooms"][1]["waiting"]] == ["first", "unknown"]
    assert board["unassigned"] == []
    assert board["rooms"][1]["waiting"][1]["room_override"] is True


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


def test_page_renders_rooms_and_unassigned_assignment_controls(tmp_path):
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
    assert "Room 1" in response.text
    assert "Room 2" in response.text
    assert "Unassigned" in response.text
    assert "unknown-key" in response.text
    assert "assign" in response.text.lower()


def test_assignment_api_places_unassigned_encounter_in_selected_room(tmp_path):
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

    assert response.status_code == 200
    assert response.json()["room_id"] == 2
    assert app.state.queue_store.get_board()["rooms"][2]["waiting"][0]["encounter_key"] == "unknown-key"
