import asyncio
import sqlite3

import httpx

from clinic_queue.app import create_app
from clinic_queue.queue import QueueStore
from clinic_queue.state import PresenceState
from tests.test_queue import create_config, snapshot


def ready_store(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    store = QueueStore(config.sqlite_path)
    encounters = [
        snapshot("one", "DOC1", patient_no="100001"),
        snapshot("two", "DOC1", patient_no="100002"),
        snapshot("three", "DOC1", patient_no="100003"),
    ]
    store.reconcile(encounters, config.doctor_room_map)
    return config, store


def test_presence_and_overdue_changes_are_saved_and_away_leaves_waiting_order(tmp_path):
    config, store = ready_store(tmp_path)

    store.set_presence("two", PresenceState.AWAY)
    board = store.get_board()
    assert [entry["encounter_key"] for entry in board["rooms"][1]["waiting"]] == ["one", "three"]
    assert [entry["encounter_key"] for entry in board["rooms"][1]["away"]] == ["two"]

    store.set_presence("two", PresenceState.PRESENT)
    store.set_overdue("one", True)
    board = store.get_board()

    assert [entry["encounter_key"] for entry in board["rooms"][1]["waiting"]] == [
        "one",
        "three",
        "two",
    ]
    assert board["rooms"][1]["waiting"][0]["overdue"] is True
    assert board["rooms"][1]["waiting"][2]["presence_override"] is True


def test_reorder_and_up_down_actions_preserve_explicit_room_order(tmp_path):
    _, store = ready_store(tmp_path)

    store.reorder("three", 1)
    store.move_down("three")
    store.move_up("two")

    board = store.get_board()
    assert [entry["encounter_key"] for entry in board["rooms"][1]["waiting"]] == [
        "one",
        "two",
        "three",
    ]
    reordered = {entry["encounter_key"]: entry["manually_reordered_at"] for entry in board["rooms"][1]["waiting"]}
    assert reordered["one"] is None
    assert reordered["two"] is not None
    assert reordered["three"] is not None


def test_call_current_and_next_patient_actions_are_room_scoped(tmp_path):
    _, store = ready_store(tmp_path)

    first = store.next_patient(1)
    assert first["encounter_key"] == "one"
    assert first["queue_status"] == "CALLED"
    assert first["current_flag"] is True

    store.set_current("one")
    second = store.next_patient(1)
    assert second["encounter_key"] == "two"
    board = store.get_board()
    room_entries = board["rooms"][1]["other"]
    assert [(entry["encounter_key"], entry["current_flag"]) for entry in room_entries] == [
        ("one", False),
        ("two", True),
    ]


def test_server_operation_order_is_last_write_wins_and_survives_restart(tmp_path):
    config, store = ready_store(tmp_path)

    store.set_presence("one", PresenceState.AWAY)
    store.set_presence("one", PresenceState.PRESENT)
    store.set_overdue("two", True)
    QueueStore(config.sqlite_path)
    restarted = QueueStore(config.sqlite_path)

    board = restarted.get_board()
    entries = board["rooms"][1]["waiting"]
    first = next(entry for entry in entries if entry["encounter_key"] == "one")
    second = next(entry for entry in entries if entry["encounter_key"] == "two")
    assert entries[-1]["encounter_key"] == "one"
    assert first["presence_status"] == "PRESENT"
    assert first["presence_override"] is True
    assert second["overdue"] is True
    with sqlite3.connect(config.sqlite_path) as connection:
        actions = [row[0] for row in connection.execute("SELECT action FROM action_log ORDER BY id")]
    assert actions == ["presence", "presence", "overdue"]


def test_drag_drop_reorder_api_accepts_target_position(tmp_path):
    config, _ = ready_store(tmp_path)
    app = create_app(config)
    app.state.queue_store.reconcile(
        [
            snapshot("one", "DOC1", patient_no="100001"),
            snapshot("two", "DOC1", patient_no="100002"),
            snapshot("three", "DOC1", patient_no="100003"),
        ],
        config.doctor_room_map,
    )

    async def reorder():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/reorder",
                json={"encounter_key": "three", "position": 1},
            )

    response = asyncio.run(reorder())

    assert response.status_code == 200
    assert [entry["encounter_key"] for entry in response.json()["rooms"]["1"]["waiting"]] == [
        "three",
        "one",
        "two",
    ]


def test_browser_action_api_changes_presence_and_renders_queue_controls(tmp_path):
    config, _ = ready_store(tmp_path)
    app = create_app(config)
    app.state.queue_store.reconcile(
        [snapshot("one", "DOC1", patient_no="100001")],
        config.doctor_room_map,
    )

    async def use_browser_actions():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            page = await client.get("/")
            away = await client.post(
                "/api/action",
                json={"encounter_key": "one", "action": "away"},
            )
            present = await client.post(
                "/api/action",
                json={"encounter_key": "one", "action": "present"},
            )
            overdue = await client.post(
                "/api/action",
                json={"encounter_key": "one", "action": "overdue", "value": True},
            )
            next_response = await client.post("/api/next", json={"room_id": 1})
            return page, away, present, overdue, next_response

    page, away, present, overdue, next_response = asyncio.run(use_browser_actions())

    assert page.status_code == 200
    assert 'data-action="away"' in page.text
    assert 'data-action="up"' in page.text
    assert 'data-next-room="1"' in page.text
    assert 'draggable="true"' in page.text
    assert away.status_code == 200
    assert away.json()["rooms"]["1"]["away"][0]["presence_status"] == "AWAY"
    assert present.status_code == 200
    returned = next(
        entry
        for entry in present.json()["rooms"]["1"]["waiting"]
        if entry["encounter_key"] == "one"
    )
    assert returned["presence_status"] == "PRESENT"
    assert overdue.status_code == 200
    marked = next(
        entry
        for entry in overdue.json()["rooms"]["1"]["waiting"]
        if entry["encounter_key"] == "one"
    )
    assert marked["overdue"] is True
    assert next_response.status_code == 200
    assert any(entry["current_flag"] for entry in next_response.json()["rooms"]["1"]["other"])
