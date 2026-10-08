import asyncio
import re
import sqlite3

import httpx

from clinic_queue.app import create_app
from clinic_queue.state import HISState, PresenceState
from tests.test_queue import create_config, snapshot


def make_ready_app(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1, "DOC2": 2})
    app = create_app(config)
    app.state.queue_store.reconcile(
        [
            snapshot("r1", "DOC1", patient_no="100001", queue_number="15"),
            snapshot("r2", "DOC2", patient_no="100002", queue_number="15"),
            snapshot("away", "DOC1", presence=PresenceState.AWAY, patient_no="100003"),
            snapshot(
                "preregistered",
                "DOC1",
                presence=PresenceState.AWAY,
                state=HISState.PREREGISTERED,
                patient_no="100007",
                queue_number="20",
            ),
            snapshot("unknown", "DOCX", patient_no="100004", queue_number="17"),
            snapshot(
                "completed",
                "DOC1",
                patient_no="100005",
                queue_number="18",
                state=HISState.COMPLETED,
            ),
            snapshot(
                "completed-room-2",
                "DOC2",
                patient_no="100006",
                queue_number="19",
                state=HISState.COMPLETED,
            ),
        ],
        config.doctor_room_map,
    )
    app.state.queue_store.set_overdue("r1", True)
    return app


def test_both_room_view_has_room_columns_and_collapsed_completed_section(tmp_path):
    app = make_ready_app(tmp_path)

    async def get_page():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/")

    response = asyncio.run(get_page())

    assert response.status_code == 200
    assert 'aria-label="一診"' in response.text
    assert 'aria-label="二診"' in response.text
    assert "#15 Name 100001" in response.text
    assert "#15 Name 100002" in response.text
    assert "未納入一診／二診篩選" in response.text
    assert "#17 Name 100004" in response.text
    assert '<details class="completed-section">' in response.text
    assert '<details class="completed-section" open' not in response.text
    assert '<details class="unconfirmed" aria-label="診別未確認">' in response.text
    assert "診別未確認 (0)" in response.text
    assert 'data-room-column="1"' in response.text
    assert 'data-room-column="2"' in response.text
    assert 'data-room-id="1" data-time-kind="1"' in response.text
    assert 'data-room-id="2" data-time-kind="1"' in response.text
    assert "已完成 (2)" in response.text
    assert "#18 Name 100005" in response.text
    assert "window.setInterval" in response.text
    assert 'fetch("/api/queue"' in response.text
    assert "}, 1000);" in response.text
    assert "NEW" in response.text


def test_room_lists_waiting_away_and_preregistered_in_order_without_preregistered_actions(tmp_path):
    app = make_ready_app(tmp_path)

    async def get_page():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/?room=1")

    response = asyncio.run(get_page())

    assert response.status_code == 200
    waiting_heading = response.text.index("<h3><span>候診中")
    away_heading = response.text.index("<h3><span>已掛暫離")
    preregistered_heading = response.text.index("<h3><span>已約未到")
    assert waiting_heading < away_heading < preregistered_heading

    preregistered_card = re.search(
        r'<article\b[^>]*data-encounter-key="preregistered"[^>]*>.*?</article>',
        response.text,
        flags=re.DOTALL,
    )
    assert preregistered_card is not None
    assert "已約未到" in preregistered_card.group(0)
    assert "data-action=" not in preregistered_card.group(0)
    away_card = re.search(
        r'<article\b[^>]*data-encounter-key="away"[^>]*>.*?</article>',
        response.text,
        flags=re.DOTALL,
    )
    assert away_card is not None
    assert 'data-action="present"' in away_card.group(0)
    assert "回候診" in away_card.group(0)
    assert "暫離" in response.text
    assert "暫未到診" not in response.text


def test_single_room_views_filter_columns_and_keep_queue_controls(tmp_path):
    app = make_ready_app(tmp_path)

    async def get_pages():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/?room=1"), await client.get("/?room=2")

    room_one, room_two = asyncio.run(get_pages())

    assert room_one.status_code == 200
    assert 'aria-label="一診"' in room_one.text
    assert 'aria-label="二診"' not in room_one.text
    assert 'data-action="up"' in room_one.text
    assert "下一位" not in room_one.text
    assert "未納入一診／二診篩選" in room_one.text
    assert "指派" not in room_one.text
    assert 'data-assign-room' not in room_one.text
    assert "#18 Name 100005" in room_one.text
    assert "#19 Name 100006" not in room_one.text
    assert "過號" in room_one.text
    assert "已掛暫離" in room_one.text
    assert "已約未到" in room_one.text
    assert "未報到" not in room_one.text
    assert "看診中" not in room_two.text
    assert "已叫號" not in room_two.text
    assert room_two.status_code == 200
    assert 'aria-label="二診"' in room_two.text
    assert 'aria-label="一診"' not in room_two.text
    assert "下一位" not in room_two.text
    assert "#19 Name 100006" in room_two.text
    assert "#18 Name 100005" not in room_two.text


def test_independent_browser_clients_observe_the_same_authoritative_queue(tmp_path):
    app = make_ready_app(tmp_path)

    async def update_and_refresh():
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://client-one") as first,
            httpx.AsyncClient(transport=transport, base_url="http://client-two") as second,
        ):
            updated = await first.post(
                "/api/action",
                json={"encounter_key": "r1", "action": "away"},
            )
            observed = await second.get("/api/queue")
            return updated, observed

    updated, observed = asyncio.run(update_and_refresh())

    assert updated.status_code == 200
    assert observed.status_code == 200
    away_entries = observed.json()["rooms"]["1"]["away"]
    assert {entry["encounter_key"] for entry in away_entries} == {"r1", "away"}
    assert [entry["encounter_key"] for entry in observed.json()["rooms"]["1"]["preregistered"]] == [
        "preregistered",
    ]
    assert observed.json()["his_stale"] is True


def test_sqlite_api_failure_returns_actionable_local_storage_message(tmp_path, monkeypatch):
    app = make_ready_app(tmp_path)

    def fail_get_board(time_kind=None):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(app.state.queue_store, "get_board", fail_get_board)

    async def get_queue():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/queue")

    response = asyncio.run(get_queue())

    assert response.status_code == 503
    assert "Local queue database unavailable" in response.json()["detail"]
    assert "app.log" in response.json()["detail"]


def test_browser_session_filters_every_list_and_keeps_unknown_sessions_separate(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1, "DOC2": 2})
    app = create_app(config)
    app.state.queue_store.reconcile(
        [
            snapshot("morning-room", "DOC1", patient_no="100101", time_kind="1"),
            snapshot("noon-room", "DOC1", patient_no="100102", time_kind="2"),
            snapshot("evening-room", "DOC2", patient_no="100103", time_kind="3"),
            snapshot("morning-away", "DOC1", presence=PresenceState.AWAY, patient_no="100104", time_kind="1"),
            snapshot("noon-away", "DOC1", presence=PresenceState.AWAY, patient_no="100105", time_kind="2"),
            snapshot("evening-away", "DOC2", presence=PresenceState.AWAY, patient_no="100113", time_kind="3"),
            snapshot("morning-preregistered", "DOC1", state=HISState.PREREGISTERED, patient_no="100118", time_kind="1"),
            snapshot("noon-preregistered", "DOC1", state=HISState.PREREGISTERED, patient_no="100119", time_kind="2"),
            snapshot("evening-preregistered", "DOC2", state=HISState.PREREGISTERED, patient_no="100120", time_kind="3"),
            snapshot("morning-unmatched", "DOCX", patient_no="100106", time_kind="1"),
            snapshot("noon-unmatched", "DOCX", patient_no="100107", time_kind="2"),
            snapshot("evening-unmatched", "DOCX", patient_no="100114", time_kind="3"),
            snapshot("morning-completed", "DOC1", patient_no="100108", state=HISState.COMPLETED, time_kind="1"),
            snapshot("noon-completed", "DOC1", patient_no="100109", state=HISState.COMPLETED, time_kind="2"),
            snapshot("evening-completed", "DOC2", patient_no="100115", state=HISState.COMPLETED, time_kind="3"),
            snapshot("unknown-room", "DOC1", patient_no="100110", time_kind="9"),
            snapshot("unknown-unmatched", "DOCX", patient_no="100111", time_kind=None),
            snapshot(
                "unknown-preregistered-room",
                "DOC1",
                state=HISState.PREREGISTERED,
                patient_no="100116",
                time_kind="9",
            ),
            snapshot(
                "unknown-preregistered-unmatched",
                "DOCX",
                state=HISState.PREREGISTERED,
                patient_no="100117",
                time_kind=None,
            ),
            snapshot("unknown-completed", "DOC1", patient_no="100112", state=HISState.COMPLETED, time_kind="0"),
        ],
        config.doctor_room_map,
    )

    async def inspect_browsers():
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://clinic") as morning_browser,
            httpx.AsyncClient(transport=transport, base_url="http://clinic") as noon_browser,
            httpx.AsyncClient(transport=transport, base_url="http://clinic") as evening_browser,
        ):
            morning_page = await morning_browser.get("/?room=1")
            morning_api = await morning_browser.get("/api/queue")
            noon_browser.cookies.set("clinic_session", "2")
            noon_page = await noon_browser.get("/?room=1")
            noon_api = await noon_browser.get("/api/queue")
            evening_browser.cookies.set("clinic_session", "3")
            evening_page = await evening_browser.get("/?room=2")
            evening_api = await evening_browser.get("/api/queue")
            morning_refresh = await morning_browser.get("/api/queue")
            return (
                morning_page,
                morning_api,
                noon_page,
                noon_api,
                evening_page,
                evening_api,
                morning_refresh,
            )

    (
        morning_page,
        morning_response,
        noon_page,
        noon_response,
        evening_page,
        evening_response,
        morning_refresh,
    ) = asyncio.run(inspect_browsers())

    assert morning_page.status_code == noon_page.status_code == 200
    assert '<option value="1" selected>早診</option>' in morning_page.text
    assert '<option value="2" selected>午診</option>' in noon_page.text
    assert '<option value="3" selected>晚診</option>' in evening_page.text
    assert "100101" in morning_page.text
    assert "100102" not in morning_page.text
    assert "100102" in noon_page.text
    assert "100101" not in noon_page.text
    assert "100103" in evening_page.text
    assert "100101" not in evening_page.text
    assert "診別未確認" in morning_page.text
    assert 'aria-label="二診"' not in morning_page.text

    morning = morning_response.json()
    noon = noon_response.json()
    assert morning_response.status_code == noon_response.status_code == 200
    assert evening_response.status_code == 200
    assert morning["selected_time_kind"] == 1
    assert noon["selected_time_kind"] == 2
    evening = evening_response.json()
    assert evening["selected_time_kind"] == 3
    assert [entry["encounter_key"] for entry in evening["rooms"]["2"]["waiting"]] == [
        "evening-room",
    ]
    assert [entry["encounter_key"] for entry in evening["rooms"]["2"]["away"]] == [
        "evening-away",
    ]
    assert [entry["encounter_key"] for entry in evening["rooms"]["2"]["preregistered"]] == [
        "evening-preregistered",
    ]
    assert [entry["encounter_key"] for entry in evening["unmatched"]["waiting"]] == [
        "evening-unmatched",
    ]
    assert [entry["encounter_key"] for entry in evening["completed"]] == [
        "evening-completed",
    ]
    assert [entry["encounter_key"] for entry in morning["rooms"]["1"]["waiting"]] == ["morning-room"]
    assert [entry["encounter_key"] for entry in morning["rooms"]["1"]["away"]] == ["morning-away"]
    assert [entry["encounter_key"] for entry in morning["rooms"]["1"]["preregistered"]] == [
        "morning-preregistered",
    ]
    assert [entry["encounter_key"] for entry in morning["unmatched"]["waiting"]] == ["morning-unmatched"]
    assert [entry["encounter_key"] for entry in morning["completed"]] == ["morning-completed"]
    assert [entry["encounter_key"] for entry in noon["rooms"]["1"]["waiting"]] == ["noon-room"]
    assert [entry["encounter_key"] for entry in noon["rooms"]["1"]["away"]] == ["noon-away"]
    assert [entry["encounter_key"] for entry in noon["rooms"]["1"]["preregistered"]] == [
        "noon-preregistered",
    ]
    assert [entry["encounter_key"] for entry in noon["unmatched"]["waiting"]] == ["noon-unmatched"]
    assert [entry["encounter_key"] for entry in noon["completed"]] == ["noon-completed"]
    assert [entry["encounter_key"] for entry in morning["unconfirmed"]["rooms"]["1"]["waiting"]] == ["unknown-room"]
    assert [entry["encounter_key"] for entry in morning["unconfirmed"]["unmatched"]["waiting"]] == ["unknown-unmatched"]
    assert [entry["encounter_key"] for entry in morning["unconfirmed"]["rooms"]["1"]["preregistered"]] == [
        "unknown-preregistered-room",
    ]
    assert [entry["encounter_key"] for entry in morning["unconfirmed"]["unmatched"]["preregistered"]] == [
        "unknown-preregistered-unmatched",
    ]
    assert [entry["encounter_key"] for entry in morning["unconfirmed"]["completed"]] == ["unknown-completed"]
    assert '<details class="unconfirmed" aria-label="診別未確認">' in morning_page.text
    assert "診別未確認 (5)" in morning_page.text
    for encounter_key in (
        "unknown-room",
        "unknown-unmatched",
        "unknown-preregistered-room",
        "unknown-preregistered-unmatched",
        "unknown-completed",
    ):
        assert len(re.findall(rf'<article\b[^>]*data-encounter-key="{encounter_key}"', morning_page.text)) == 1
    for encounter_key in ("unknown-preregistered-room", "unknown-preregistered-unmatched"):
        preregistered_card = re.search(
            rf'<article\b[^>]*data-encounter-key="{encounter_key}"[^>]*>.*?</article>',
            morning_page.text,
            flags=re.DOTALL,
        )
        assert preregistered_card is not None
        assert "data-action=" not in preregistered_card.group(0)
    assert morning_refresh.json()["selected_time_kind"] == 1


def test_shared_room_filters_move_records_and_survive_restart(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1, "DOC2": 2})
    app = create_app(config)
    app.state.queue_store.reconcile(
        [
            snapshot("room-one", "DOC1", patient_no="100201"),
            snapshot("matched-later", "DOC1", patient_no="100202"),
            snapshot("matched-away", "DOC1", patient_no="100203"),
            snapshot("room-two", "DOC2", patient_no="100204"),
            snapshot("unmatched-completed", "DOCY", patient_no="100205", state=HISState.COMPLETED),
        ],
        config.doctor_room_map,
    )
    async def save_filter_and_read_from_another_workstation():
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://clinic") as first,
            httpx.AsyncClient(transport=transport, base_url="http://clinic") as second,
        ):
            await first.post(
                "/api/action",
                json={"encounter_key": "matched-later", "action": "overdue", "value": True},
            )
            await first.post(
                "/api/action",
                json={"encounter_key": "matched-away", "action": "away"},
            )
            await first.post(
                "/api/action",
                json={"encounter_key": "matched-away", "action": "overdue", "value": True},
            )
            app.state.queue_store.reconcile(
                [
                    snapshot("room-one", "DOC1", patient_no="100201"),
                    snapshot("matched-later", "DOCX", patient_no="100202"),
                    snapshot("matched-away", "DOCX", patient_no="100203"),
                    snapshot("room-two", "DOC2", patient_no="100204"),
                    snapshot("unmatched-completed", "DOCY", patient_no="100205", state=HISState.COMPLETED),
                ],
                config.doctor_room_map,
            )
            saved = await first.post(
                "/api/room-doctor-code",
                json={"room_id": 1, "doctor_code": "DOCX"},
            )
            shared = await second.get("/api/queue")
            duplicate = await second.post(
                "/api/room-doctor-code",
                json={"room_id": 2, "doctor_code": "DOCX"},
            )
            page = await second.get("/")
            return saved, shared, duplicate, page

    saved, shared, duplicate, page = asyncio.run(save_filter_and_read_from_another_workstation())

    assert saved.status_code == shared.status_code == 200
    assert saved.json()["room_doctor_codes"] == {"1": "DOCX", "2": "DOC2"}
    assert shared.json()["room_doctor_codes"] == {"1": "DOCX", "2": "DOC2"}
    assert [entry["encounter_key"] for entry in shared.json()["rooms"]["1"]["waiting"]] == [
        "matched-later",
    ]
    assert shared.json()["rooms"]["1"]["waiting"][0]["overdue"] is True
    assert [entry["encounter_key"] for entry in shared.json()["rooms"]["1"]["away"]] == [
        "matched-away",
    ]
    assert shared.json()["rooms"]["1"]["away"][0]["overdue"] is True
    assert [entry["encounter_key"] for entry in shared.json()["unmatched"]["waiting"]] == [
        "room-one",
    ]
    assert [entry["encounter_key"] for entry in shared.json()["rooms"]["2"]["waiting"]] == [
        "room-two",
    ]
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "This physician code is already selected for the other room."
    assert page.status_code == 200
    assert 'data-encounter-key="unmatched-completed"' in page.text
    unmatched_completed_card = re.search(
        r'<article\b[^>]*data-encounter-key="unmatched-completed"[^>]*>.*?</article>',
        page.text,
        flags=re.DOTALL,
    )
    assert unmatched_completed_card is not None
    assert "data-action" not in unmatched_completed_card.group(0)

    restarted_app = create_app(config)

    async def read_after_restart():
        transport = httpx.ASGITransport(app=restarted_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://clinic") as client:
            return await client.get("/api/queue")

    restarted = asyncio.run(read_after_restart())
    assert restarted.status_code == 200
    assert restarted.json()["room_doctor_codes"] == {"1": "DOCX", "2": "DOC2"}


def test_blank_room_filter_has_no_queue_and_assignment_api_is_absent(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    app = create_app(config)
    app.state.queue_store.reconcile(
        [snapshot("room-one", "DOC1"), snapshot("unmatched", "DOCX")],
        config.doctor_room_map,
    )

    async def clear_filter_and_check_removed_assignment_api():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://clinic") as client:
            cleared = await client.post(
                "/api/room-doctor-code",
                json={"room_id": 1, "doctor_code": "   "},
            )
            assignment = await client.post(
                "/api/assign",
                json={"encounter_key": "unmatched", "room_id": 1},
            )
            page = await client.get("/")
            return cleared, assignment, page

    cleared, assignment, page = asyncio.run(clear_filter_and_check_removed_assignment_api())

    assert cleared.status_code == 200
    assert cleared.json()["room_doctor_codes"]["1"] == ""
    assert cleared.json()["rooms"]["1"]["waiting"] == []
    assert {entry["encounter_key"] for entry in cleared.json()["unmatched"]["waiting"]} == {
        "room-one",
        "unmatched",
    }
    assert assignment.status_code == 404
    assert page.status_code == 200
    assert "指派" not in page.text
