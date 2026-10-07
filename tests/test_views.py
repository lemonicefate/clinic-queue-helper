import asyncio
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
    app.state.queue_store.set_current("r2")
    return app


def test_both_room_view_has_room_columns_and_collapsed_completed_section(tmp_path):
    app = make_ready_app(tmp_path)

    async def get_page():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/")

    response = asyncio.run(get_page())

    assert response.status_code == 200
    assert 'aria-label="Room 1"' in response.text
    assert 'aria-label="Room 2"' in response.text
    assert "#15 Name 100001" in response.text
    assert "#15 Name 100002" in response.text
    assert "Unassigned" in response.text
    assert "#17 Name 100004" in response.text
    assert '<details class="completed-section">' in response.text
    assert '<details class="completed-section" open' not in response.text
    assert 'data-room-column="1"' in response.text
    assert 'data-room-column="2"' in response.text
    assert "已完成 (2)" in response.text
    assert "#18 Name 100005" in response.text
    assert "window.setInterval" in response.text
    assert 'fetch("/api/queue"' in response.text
    assert "}, 1000);" in response.text
    assert "NEW" in response.text


def test_single_room_views_filter_columns_and_keep_queue_controls(tmp_path):
    app = make_ready_app(tmp_path)

    async def get_pages():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/?room=1"), await client.get("/?room=2")

    room_one, room_two = asyncio.run(get_pages())

    assert room_one.status_code == 200
    assert 'aria-label="Room 1"' in room_one.text
    assert 'aria-label="Room 2"' not in room_one.text
    assert 'data-action="up"' in room_one.text
    assert 'data-next-room="1"' in room_one.text
    assert "Unassigned" in room_one.text
    assert "指派 Room 1" in room_one.text
    assert "#18 Name 100005" in room_one.text
    assert "#19 Name 100006" not in room_one.text
    assert "過號" in room_one.text
    assert "暫未到診" in room_one.text
    assert "看診中" in room_two.text
    assert room_two.status_code == 200
    assert 'aria-label="Room 2"' in room_two.text
    assert 'aria-label="Room 1"' not in room_two.text
    assert 'data-next-room="2"' in room_two.text
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
    assert observed.json()["his_stale"] is True


def test_sqlite_api_failure_returns_actionable_local_storage_message(tmp_path, monkeypatch):
    app = make_ready_app(tmp_path)

    def fail_get_board():
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
