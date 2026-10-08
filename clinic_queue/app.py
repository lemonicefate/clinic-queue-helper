"""FastAPI application factory."""

import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import sqlite3
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .config import AppConfig
from .polling import HISPoller
from .queue import QueueError, QueueStore
from .state import ClinicSession, PresenceState, normalize_time_kind


_TEMPLATE_DIR = Path(__file__).with_name("templates")
logger = logging.getLogger("clinic_queue.app")


def _selected_time_kind(request: Request) -> ClinicSession:
    return normalize_time_kind(request.cookies.get("clinic_session")) or ClinicSession.MORNING


class RoomDoctorCodeUpdate(BaseModel):
    room_id: int
    doctor_code: str


class QueueAction(BaseModel):
    encounter_key: str
    action: str
    value: bool | None = None


class QueueReorder(BaseModel):
    encounter_key: str
    position: int | None = None
    target_encounter_key: str | None = None
    insert_after: bool | None = None


def create_app(config: AppConfig) -> FastAPI:
    """Create the browser application with an already validated configuration."""
    store = QueueStore(config.sqlite_path)
    store.initialize_room_doctor_codes(config.doctor_room_map)
    poller = HISPoller(config, store)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(poller.run())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    application = FastAPI(title="Clinic Queue Helper", lifespan=lifespan)
    application.state.config = config
    application.state.queue_store = store
    application.state.his_poller = poller
    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
    poller.initialize()

    @application.exception_handler(sqlite3.Error)
    async def sqlite_error_handler(request: Request, exc: sqlite3.Error) -> JSONResponse:
        logger.error(
            "Local SQLite queue database operation failed: %s",
            exc,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": f"Local queue database unavailable. Check the server log at {config.log_path}."
            },
        )

    @application.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        room_param = request.query_params.get("room")
        selected_room = int(room_param) if room_param in ("1", "2") else None
        selected_time_kind = _selected_time_kind(request)
        board = store.get_board(selected_time_kind)
        completed_encounters = [
            encounter
            for encounter in board["completed"]
            if selected_room is None or encounter["room_id"] in (None, selected_room)
        ]
        unconfirmed_completed_encounters = [
            encounter
            for encounter in board["unconfirmed"]["completed"]
            if encounter["room_id"] is not None
            and (selected_room is None or encounter["room_id"] == selected_room)
        ]
        visible_room_ids = [selected_room] if selected_room is not None else [1, 2]
        unconfirmed_count = sum(
            len(board["unconfirmed"]["rooms"][room_id][status])
            for room_id in visible_room_ids
            for status in ("waiting", "away", "preregistered")
        ) + sum(
            len(board["unconfirmed"]["unmatched"][status])
            for status in ("waiting", "away", "preregistered", "completed")
        ) + len(unconfirmed_completed_encounters)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "title": "Clinic Queue Helper",
                "board": board,
                "his_stale": poller.his_stale,
                "selected_rooms": [selected_room] if selected_room is not None else [1, 2],
                "selected_room": selected_room,
                "selected_time_kind": selected_time_kind,
                "completed_encounters": completed_encounters,
                "unconfirmed_completed_encounters": unconfirmed_completed_encounters,
                "unconfirmed_count": unconfirmed_count,
                "browser_refresh_ms": config.browser_refresh_ms,
                "storage_error": poller.storage_error,
                "queue_state": {
                    **board,
                    "his_stale": poller.his_stale,
                    "storage_error": poller.storage_error,
                },
            },
        )

    @application.get("/api/queue")
    async def get_queue(request: Request) -> dict:
        return {
            **store.get_board(_selected_time_kind(request)),
            "his_stale": poller.his_stale,
            "storage_error": poller.storage_error,
        }

    @application.post("/api/room-doctor-code")
    async def update_room_doctor_code(update: RoomDoctorCodeUpdate, request: Request) -> dict:
        try:
            store.set_room_doctor_code(
                update.room_id,
                update.doctor_code,
            )
        except QueueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            **store.get_board(_selected_time_kind(request)),
            "his_stale": poller.his_stale,
            "storage_error": poller.storage_error,
        }

    @application.post("/api/action")
    async def queue_action(action: QueueAction, request: Request) -> dict:
        store = application.state.queue_store
        try:
            if action.action == "present":
                store.set_presence(action.encounter_key, PresenceState.PRESENT)
            elif action.action == "away":
                store.set_presence(action.encounter_key, PresenceState.AWAY)
            elif action.action == "overdue":
                if action.value is None:
                    raise QueueError("overdue action requires a boolean value.")
                store.set_overdue(action.encounter_key, action.value)
            elif action.action == "up":
                store.move_up(action.encounter_key)
            elif action.action == "down":
                store.move_down(action.encounter_key)
            else:
                raise QueueError(f"Unknown queue action: {action.action}")
            return store.get_board(_selected_time_kind(request))
        except QueueError as exc:
            status_code = 404 if "not found" in str(exc).lower() else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @application.post("/api/reorder")
    async def reorder_queue(reorder: QueueReorder, request: Request) -> dict:
        try:
            if reorder.target_encounter_key is not None:
                if reorder.position is not None or reorder.insert_after is None:
                    raise QueueError("A relative reorder requires only a target and insert_after value.")
                application.state.queue_store.reorder_relative(
                    reorder.encounter_key,
                    reorder.target_encounter_key,
                    reorder.insert_after,
                )
            else:
                if reorder.position is None or reorder.insert_after is not None:
                    raise QueueError("A reorder requires a final position or a target insertion position.")
                application.state.queue_store.reorder(reorder.encounter_key, reorder.position)
            return application.state.queue_store.get_board(_selected_time_kind(request))
        except QueueError as exc:
            status_code = 404 if "not found" in str(exc).lower() else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    return application
