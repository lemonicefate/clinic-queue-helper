import asyncio
import json
import sqlite3
from datetime import date

import httpx

from clinic_queue.app import create_app
from clinic_queue.queue import QueueStore
from clinic_queue.state import HISState, PresenceState
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
    assert [entry["queue_position"] for entry in board["rooms"][1]["waiting"]] == [1, 2]
    assert [entry["encounter_key"] for entry in board["rooms"][1]["away"]] == ["two"]

    store.set_presence("two", PresenceState.PRESENT)
    store.set_overdue("one", True)
    board = store.get_board()

    assert [entry["encounter_key"] for entry in board["rooms"][1]["waiting"]] == [
        "one",
        "three",
        "two",
    ]
    assert [entry["queue_position"] for entry in board["rooms"][1]["waiting"]] == [1, 2, 3]
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


def test_queue_exposes_only_his_supported_visit_categories(tmp_path):
    _, store = ready_store(tmp_path)
    store.reconcile(
        [
            snapshot("waiting", "DOC1", patient_no="100001"),
            snapshot("preregistered", "DOC1", patient_no="100002", state=HISState.PREREGISTERED),
            snapshot("completed", "DOC1", patient_no="100003", state=HISState.COMPLETED),
        ],
        {"DOC1": 1},
    )

    board = store.get_board()
    entries = (
        board["rooms"][1]["waiting"]
        + board["rooms"][1]["away"]
        + board["rooms"][1]["preregistered"]
        + board["completed"]
    )

    assert {entry["queue_status"] for entry in entries} == {
        "WAITING",
        "PREREGISTERED",
        "COMPLETED",
    }
    assert all("current_flag" not in entry for entry in entries)


def test_queue_api_separates_preregistration_from_staff_temporary_away(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    app = create_app(config)
    app.state.queue_store.reconcile(
        [
            snapshot("waiting", "DOC1", patient_no="100001"),
            snapshot("temporary-away", "DOC1", presence=PresenceState.AWAY, patient_no="100002"),
            snapshot("preregistered", "DOC1", state=HISState.PREREGISTERED, patient_no="100003"),
            snapshot("completed", "DOC1", state=HISState.COMPLETED, patient_no="100004"),
        ],
        config.doctor_room_map,
    )

    async def get_queue():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/queue")

    response = asyncio.run(get_queue())

    assert response.status_code == 200
    board = response.json()
    assert [entry["encounter_key"] for entry in board["rooms"]["1"]["waiting"]] == ["waiting"]
    assert [entry["encounter_key"] for entry in board["rooms"]["1"]["away"]] == ["temporary-away"]
    assert [entry["encounter_key"] for entry in board["rooms"]["1"]["preregistered"]] == ["preregistered"]
    assert [entry["encounter_key"] for entry in board["completed"]] == ["completed"]


def test_his_update_moves_preregistered_encounter_to_waiting_at_tail(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    app = create_app(config)
    store = app.state.queue_store
    store.reconcile(
        [
            snapshot("first", "DOC1", patient_no="100001"),
            snapshot("preregistered", "DOC1", presence=None, state=HISState.PREREGISTERED, patient_no="100002"),
            snapshot("second", "DOC1", patient_no="100003"),
        ],
        config.doctor_room_map,
    )

    async def read_queue():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            before = await client.get("/api/queue")
            store.reconcile(
                [
                    snapshot("first", "DOC1", patient_no="100001"),
                    snapshot("preregistered", "DOC1", patient_no="100002"),
                    snapshot("second", "DOC1", patient_no="100003"),
                ],
                config.doctor_room_map,
            )
            after = await client.get("/api/queue")
            return before, after

    before, after = asyncio.run(read_queue())

    assert before.status_code == after.status_code == 200
    assert [entry["encounter_key"] for entry in before.json()["rooms"]["1"]["waiting"]] == [
        "first",
        "second",
    ]
    assert [entry["encounter_key"] for entry in before.json()["rooms"]["1"]["preregistered"]] == [
        "preregistered",
    ]
    after_waiting = after.json()["rooms"]["1"]["waiting"]
    assert [entry["encounter_key"] for entry in after_waiting] == ["first", "second", "preregistered"]
    assert after_waiting[-1]["queue_position"] == 3


def test_api_rejects_presence_actions_for_preregistered_encounters(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    app = create_app(config)
    app.state.queue_store.reconcile(
        [snapshot("preregistered", "DOC1", state=HISState.PREREGISTERED)],
        config.doctor_room_map,
    )

    async def change_presence():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            returned = await client.post(
                "/api/action",
                json={"encounter_key": "preregistered", "action": "present"},
            )
            away = await client.post(
                "/api/action",
                json={"encounter_key": "preregistered", "action": "away"},
            )
            return returned, away

    returned, away = asyncio.run(change_presence())

    assert returned.status_code == 400
    assert away.status_code == 400


def test_return_to_queue_appends_after_sync_and_preserves_other_waiters(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    app = create_app(config)
    app.state.queue_store.reconcile(
        [
            snapshot("one", "DOC1", patient_no="100001"),
            snapshot("returning", "DOC1", patient_no="100002"),
            snapshot("three", "DOC1", patient_no="100003"),
            snapshot("preregistered", "DOC1", state=HISState.PREREGISTERED, patient_no="100004"),
        ],
        config.doctor_room_map,
    )

    async def exercise_return():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            away = await client.post(
                "/api/action",
                json={"encounter_key": "returning", "action": "away"},
            )
            app.state.queue_store.reconcile(
                [
                    snapshot("one", "DOC1", patient_no="100001"),
                    snapshot("returning", "DOC1", patient_no="100002"),
                    snapshot("three", "DOC1", patient_no="100003"),
                    snapshot("preregistered", "DOC1", state=HISState.PREREGISTERED, patient_no="100004"),
                ],
                config.doctor_room_map,
            )
            after_sync = await client.get("/api/queue")
            returned = await client.post(
                "/api/action",
                json={"encounter_key": "returning", "action": "present"},
            )
            return away, after_sync, returned

    away, after_sync, returned = asyncio.run(exercise_return())

    assert away.status_code == 200
    assert after_sync.status_code == 200
    assert [entry["encounter_key"] for entry in after_sync.json()["rooms"]["1"]["waiting"]] == [
        "one",
        "three",
    ]
    assert [entry["encounter_key"] for entry in after_sync.json()["rooms"]["1"]["away"]] == [
        "returning",
    ]
    assert returned.status_code == 200
    assert [entry["encounter_key"] for entry in returned.json()["rooms"]["1"]["waiting"]] == [
        "one",
        "three",
        "returning",
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


def test_drag_reorder_inserts_relative_to_target_and_persists_after_restart(tmp_path):
    config, _ = ready_store(tmp_path)
    app = create_app(config)

    async def apply_drop_positions():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            before = await client.post(
                "/api/reorder",
                json={
                    "encounter_key": "one",
                    "target_encounter_key": "three",
                    "insert_after": False,
                },
            )
            after = await client.post(
                "/api/reorder",
                json={
                    "encounter_key": "one",
                    "target_encounter_key": "three",
                    "insert_after": True,
                },
            )
            return before, after

    before, after = asyncio.run(apply_drop_positions())

    assert before.status_code == 200
    assert [entry["encounter_key"] for entry in before.json()["rooms"]["1"]["waiting"]] == [
        "two",
        "one",
        "three",
    ]
    assert after.status_code == 200
    assert [entry["encounter_key"] for entry in after.json()["rooms"]["1"]["waiting"]] == [
        "two",
        "three",
        "one",
    ]
    restarted = QueueStore(config.sqlite_path)
    assert [entry["encounter_key"] for entry in restarted.get_board()["rooms"][1]["waiting"]] == [
        "two",
        "three",
        "one",
    ]


def test_drag_reorder_api_rejects_target_from_another_session(tmp_path):
    config, _ = ready_store(tmp_path)
    app = create_app(config)
    app.state.queue_store.reconcile(
        [
            snapshot("one", "DOC1", patient_no="100001"),
            snapshot("two", "DOC1", patient_no="100002"),
            snapshot("three", "DOC1", patient_no="100003"),
            snapshot("noon", "DOC1", patient_no="100004", time_kind="2"),
        ],
        config.doctor_room_map,
    )

    async def drop_across_sessions():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/reorder",
                json={
                    "encounter_key": "one",
                    "target_encounter_key": "noon",
                    "insert_after": False,
                },
            )

    response = asyncio.run(drop_across_sessions())

    assert response.status_code == 400


def test_waiting_cards_are_keyboard_focusable(tmp_path):
    config, _ = ready_store(tmp_path)
    app = create_app(config)
    app.state.queue_store.reconcile(
        [snapshot("one", "DOC1", patient_no="100001")],
        config.doctor_room_map,
    )

    async def get_board():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/")

    response = asyncio.run(get_board())

    assert response.status_code == 200
    card = response.text.split('data-encounter-key="one"', maxsplit=1)[1].split(">", maxsplit=1)[0]
    assert 'tabindex="0"' in card


def test_reorder_api_rejects_preregistered_encounters(tmp_path):
    config, _ = ready_store(tmp_path)
    app = create_app(config)
    app.state.queue_store.reconcile(
        [
            snapshot("waiting", "DOC1", patient_no="100001"),
            snapshot(
                "preregistered",
                "DOC1",
                presence=PresenceState.AWAY,
                state=HISState.PREREGISTERED,
                patient_no="100002",
            ),
        ],
        config.doctor_room_map,
    )
    async def reorder_preregistered():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/reorder",
                json={"encounter_key": "preregistered", "position": 1},
            )

    response = asyncio.run(reorder_preregistered())

    assert response.status_code == 400


def test_browser_action_api_keeps_local_controls_without_calling_actions(tmp_path):
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
            removed_action = await client.post(
                "/api/action",
                json={"encounter_key": "one", "action": "call"},
            )
            removed_endpoint = await client.post("/api/next", json={"room_id": 1})
            return page, away, present, overdue, removed_action, removed_endpoint

    page, away, present, overdue, removed_action, removed_endpoint = asyncio.run(use_browser_actions())

    assert page.status_code == 200
    assert 'data-action="away"' in page.text
    assert "暫離" in page.text
    assert "暫未到診" not in page.text
    assert 'data-action="up"' in page.text
    assert "下一位" not in page.text
    assert "叫號" not in page.text
    assert "設為目前看診" not in page.text
    assert "看診中" not in page.text
    assert "已叫號" not in page.text
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
    assert removed_action.status_code == 400
    assert removed_endpoint.status_code == 404


def test_room_session_order_actions_do_not_change_other_groups(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1, "DOC2": 2})
    app = create_app(config)
    app.state.queue_store.reconcile(
        [
            snapshot("morning-one", "DOC1", patient_no="100001", time_kind="1"),
            snapshot("morning-two", "DOC1", patient_no="100002", time_kind="1"),
            snapshot("morning-extra", "DOCX", patient_no="100003", time_kind="1"),
            snapshot("noon-one", "DOC1", patient_no="100004", time_kind="2"),
            snapshot("noon-two", "DOC1", patient_no="100005", time_kind="2"),
            snapshot("noon-room-two", "DOC2", patient_no="100006", time_kind="2"),
        ],
        config.doctor_room_map,
    )

    async def exercise_room_orders():
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://clinic") as morning_browser,
            httpx.AsyncClient(transport=transport, base_url="http://clinic") as noon_browser,
        ):
            noon_browser.cookies.set("clinic_session", "2")
            await morning_browser.post(
                "/api/reorder",
                json={"encounter_key": "morning-two", "position": 1},
            )
            return await morning_browser.get("/api/queue"), await noon_browser.get("/api/queue")

    morning_response, noon_response = asyncio.run(exercise_room_orders())
    morning = morning_response.json()
    noon = noon_response.json()

    assert morning_response.status_code == noon_response.status_code == 200
    assert [entry["encounter_key"] for entry in morning["rooms"]["1"]["waiting"]] == [
        "morning-two",
        "morning-one",
    ]
    assert [entry["queue_position"] for entry in morning["rooms"]["1"]["waiting"]] == [1, 2]
    assert [entry["encounter_key"] for entry in morning["unmatched"]["waiting"]] == [
        "morning-extra",
    ]
    assert [entry["encounter_key"] for entry in noon["rooms"]["1"]["waiting"]] == [
        "noon-one",
        "noon-two",
    ]
    assert [entry["encounter_key"] for entry in noon["rooms"]["2"]["waiting"]] == [
        "noon-room-two",
    ]


def test_old_room_order_migrates_into_contiguous_session_orders(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"
    columns = [
        "encounter_key",
        "his_recno",
        "patient_no",
        "patient_name",
        "doctor_code",
        "visit_date",
        "registration_time",
        "his_gino1_raw",
        "queue_number",
        "his_state",
        "raw_his_json",
        "room_id",
        "presence_status",
        "queue_status",
        "queue_position",
        "overdue",
        "new_highlight_until",
        "manually_reordered_at",
        "current_flag",
        "room_override",
        "presence_override",
        "created_at",
        "updated_at",
    ]
    records = [
        ("morning-one", "1", "WAITING", 1, "N", ""),
        ("noon-one", "2", "CALLED", 2, "N", ""),
        ("noon-two", "2", "IN_CONSULTATION", 3, "B", ""),
        ("morning-two", "1", "WAITING", 4, "N", ""),
        ("morning-preregistered", "1", "WAITING", 5, "C", ""),
    ]
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE encounter_state (
                encounter_key TEXT PRIMARY KEY,
                his_recno INTEGER NOT NULL,
                patient_no TEXT NOT NULL,
                patient_name TEXT NOT NULL,
                doctor_code TEXT NOT NULL,
                visit_date TEXT NOT NULL,
                registration_time TEXT NOT NULL,
                his_gino1_raw TEXT NOT NULL,
                queue_number TEXT NOT NULL,
                his_state TEXT NOT NULL,
                raw_his_json TEXT NOT NULL,
                room_id INTEGER CHECK (room_id IN (1, 2) OR room_id IS NULL),
                presence_status TEXT NOT NULL CHECK (presence_status IN ('PRESENT', 'AWAY')),
                queue_status TEXT NOT NULL DEFAULT 'WAITING',
                queue_position INTEGER,
                overdue INTEGER NOT NULL DEFAULT 0,
                new_highlight_until TEXT,
                manually_reordered_at TEXT,
                current_flag INTEGER NOT NULL DEFAULT 0,
                room_override INTEGER NOT NULL DEFAULT 0,
                presence_override INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        insert_sql = (
            f"INSERT INTO encounter_state ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})"
        )
        for index, (key, time_kind, old_status, position, treat, over) in enumerate(records):
            raw_his = {
                "TIME_KIND": time_kind,
                "TREAT": treat,
                "OVER": over,
                "_DELETED": "False",
            }
            values = (
                key,
                index + 1,
                f"10000{index + 1}",
                f"Patient {index + 1}",
                "DOC1",
                date.today().isoformat(),
                "0900",
                "1510070001",
                str(index + 1),
                "IN_PROGRESS" if treat == "B" else "WAITING",
                json.dumps(raw_his),
                1,
                "PRESENT",
                old_status,
                position,
                int(key == "noon-two"),
                None,
                None,
                int(old_status in {"CALLED", "IN_CONSULTATION"}),
                1,
                int(key != "morning-preregistered"),
                f"2026-01-01T00:00:0{index}Z",
                f"2026-01-01T00:00:0{index}Z",
            )
            connection.execute(insert_sql, values)

    migrated = QueueStore(database_path)
    morning = migrated.get_board(1)["rooms"][1]["waiting"]
    morning_preregistered = migrated.get_board(1)["rooms"][1]["preregistered"]
    noon = migrated.get_board(2)["rooms"][1]["waiting"]

    assert [entry["encounter_key"] for entry in morning] == ["morning-one", "morning-two"]
    assert [entry["queue_position"] for entry in morning] == [1, 2]
    assert [entry["encounter_key"] for entry in noon] == ["noon-one", "noon-two"]
    assert [entry["queue_position"] for entry in noon] == [1, 2]
    assert all(entry["queue_status"] == "WAITING" for entry in morning + noon)
    assert morning_preregistered[0]["encounter_key"] == "morning-preregistered"
    assert morning_preregistered[0]["queue_status"] == "PREREGISTERED"
    assert morning_preregistered[0]["queue_position"] is None
    with sqlite3.connect(database_path) as connection:
        migrated_sessions = dict(
            connection.execute("SELECT encounter_key, time_kind FROM encounter_state")
        )
        current_flags = [row[0] for row in connection.execute("SELECT current_flag FROM encounter_state")]
    assert migrated_sessions == {
        "morning-one": 1,
        "noon-one": 2,
        "noon-two": 2,
        "morning-two": 1,
        "morning-preregistered": 1,
    }
    assert current_flags == [0, 0, 0, 0, 0]
