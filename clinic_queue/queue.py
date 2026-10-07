"""SQLite persistence and routing for application-owned encounter state."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .his import EncounterSnapshot
from .state import HISState, PresenceState


logger = logging.getLogger("clinic_queue.queue")


class QueueError(ValueError):
    """Raised when a queue operation cannot be applied to stored state."""


class QueueStore:
    """Keep queue-owned fields in SQLite, separate from read-only HIS files."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS encounter_state (
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS action_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    encounter_key TEXT NOT NULL,
                    action TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT
                )
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _new_room_position(connection: sqlite3.Connection, room_id: int) -> int:
        result = connection.execute(
            """
            SELECT COALESCE(MAX(queue_position), 0) + 1
            FROM encounter_state
            WHERE room_id = ? AND presence_status = 'PRESENT'
              AND queue_status NOT IN ('COMPLETED', 'INVALIDATED')
            """,
            (room_id,),
        ).fetchone()
        return int(result[0])

    @staticmethod
    def _normalize_room_positions(
        connection: sqlite3.Connection,
        room_id: int,
        timestamp: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT encounter_key, queue_position FROM encounter_state
            WHERE room_id = ? AND presence_status = 'PRESENT'
              AND queue_status NOT IN ('COMPLETED', 'INVALIDATED')
            ORDER BY CASE WHEN queue_position IS NULL THEN 1 ELSE 0 END,
                     queue_position, created_at, encounter_key
            """,
            (room_id,),
        ).fetchall()
        changed_positions = [
            (position, timestamp, str(row["encounter_key"]))
            for position, row in enumerate(rows, start=1)
            if row["queue_position"] != position
        ]
        connection.executemany(
            """
            UPDATE encounter_state
            SET queue_position = ?, updated_at = ?
            WHERE encounter_key = ?
            """,
            changed_positions,
        )

    def reconcile(
        self,
        encounters: Sequence[EncounterSnapshot],
        doctor_room_map: Mapping[str, int],
        *,
        new_highlight_seconds: int = 300,
        clinic_date: date | None = None,
        unreadable_recno: Sequence[int] = (),
    ) -> None:
        """Upsert HIS state, preserve staff overrides, and optionally prune a full-day scan."""
        timestamp = self._now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current_keys = {encounter.encounter_key for encounter in encounters}
            for encounter in encounters:
                mapped_room = doctor_room_map.get(encounter.doctor_code)
                if mapped_room not in (1, 2):
                    mapped_room = None
                    logger.warning(
                        "No room mapping for doctor %r; encounter %s is Unassigned",
                        encounter.doctor_code,
                        encounter.encounter_key,
                    )

                existing = connection.execute(
                    "SELECT * FROM encounter_state WHERE encounter_key = ?",
                    (encounter.encounter_key,),
                ).fetchone()
                if existing is None:
                    room_id = mapped_room
                    presence = encounter.initial_presence or PresenceState.PRESENT
                    position = (
                        self._new_room_position(connection, room_id)
                        if room_id is not None and presence is PresenceState.PRESENT
                        else None
                    )
                    created_at = timestamp
                    room_override = 0
                    presence_override = 0
                    overdue = 0
                    highlight_until = None
                    if encounter.active and new_highlight_seconds > 0:
                        highlight_until = (
                            datetime.now(timezone.utc) + timedelta(seconds=new_highlight_seconds)
                        ).isoformat(timespec="seconds")
                    reordered_at = None
                    current_flag = 0
                else:
                    room_override = int(existing["room_override"])
                    presence_override = int(existing["presence_override"])
                    room_id = existing["room_id"] if room_override else mapped_room
                    presence = (
                        PresenceState(existing["presence_status"])
                        if presence_override
                        else encounter.initial_presence or PresenceState.PRESENT
                    )
                    old_position = existing["queue_position"]
                    same_room = existing["room_id"] == room_id
                    if room_id is None or presence is PresenceState.AWAY:
                        position = None
                    elif same_room and old_position is not None:
                        position = int(old_position)
                    else:
                        position = self._new_room_position(connection, room_id)
                    created_at = str(existing["created_at"])
                    overdue = int(existing["overdue"])
                    highlight_until = existing["new_highlight_until"]
                    reordered_at = existing["manually_reordered_at"]
                    current_flag = int(existing["current_flag"])

                queue_status = {
                    HISState.INVALID: "INVALIDATED",
                    HISState.COMPLETED: "COMPLETED",
                    HISState.IN_PROGRESS: "IN_CONSULTATION",
                }.get(encounter.his_state, "WAITING")
                if existing is not None and queue_status == "WAITING":
                    prior_status = str(existing["queue_status"])
                    if prior_status not in ("COMPLETED", "INVALIDATED"):
                        queue_status = prior_status
                if queue_status in ("COMPLETED", "INVALIDATED"):
                    position = None
                    current_flag = 0

                connection.execute(
                    """
                    INSERT INTO encounter_state (
                        encounter_key, his_recno, patient_no, patient_name, doctor_code,
                        visit_date, registration_time, his_gino1_raw, queue_number,
                        his_state, raw_his_json, room_id, presence_status, queue_status,
                        queue_position, overdue, new_highlight_until, manually_reordered_at,
                        current_flag, room_override, presence_override, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(encounter_key) DO UPDATE SET
                        his_recno = excluded.his_recno,
                        patient_no = excluded.patient_no,
                        patient_name = excluded.patient_name,
                        doctor_code = excluded.doctor_code,
                        visit_date = excluded.visit_date,
                        registration_time = excluded.registration_time,
                        his_gino1_raw = excluded.his_gino1_raw,
                        queue_number = excluded.queue_number,
                        his_state = excluded.his_state,
                        raw_his_json = excluded.raw_his_json,
                        room_id = excluded.room_id,
                        presence_status = excluded.presence_status,
                        queue_status = excluded.queue_status,
                        queue_position = excluded.queue_position,
                        overdue = excluded.overdue,
                        new_highlight_until = excluded.new_highlight_until,
                        manually_reordered_at = excluded.manually_reordered_at,
                        current_flag = excluded.current_flag,
                        room_override = excluded.room_override,
                        presence_override = excluded.presence_override,
                        updated_at = excluded.updated_at
                    """,
                    (
                        encounter.encounter_key,
                        encounter.recno,
                        encounter.patient_no,
                        encounter.patient_name,
                        encounter.doctor_code,
                        encounter.visit_date.isoformat(),
                        encounter.registration_time,
                        encounter.gino1_raw,
                        encounter.queue_number,
                        encounter.his_state.value,
                        json.dumps(encounter.raw_his, ensure_ascii=False),
                        room_id,
                        presence.value,
                        queue_status,
                        position,
                        overdue,
                        highlight_until,
                        reordered_at,
                        current_flag,
                        room_override,
                        presence_override,
                        created_at,
                        timestamp,
                    ),
                )
                if queue_status in ("COMPLETED", "INVALIDATED") and (
                    existing is None or existing["queue_status"] != queue_status
                ):
                    action = "complete" if queue_status == "COMPLETED" else "invalidate"
                    self._log_action(connection, encounter.encounter_key, action, None, queue_status)
                    logger.info("Encounter %s %s", encounter.encounter_key, action)

            if clinic_date is not None:
                connection.execute(
                    "DELETE FROM encounter_state WHERE visit_date <> ?",
                    (clinic_date.isoformat(),),
                )
                unreadable = set(unreadable_recno)
                stale_rows = connection.execute(
                    "SELECT encounter_key, his_recno FROM encounter_state WHERE visit_date = ?",
                    (clinic_date.isoformat(),),
                ).fetchall()
                stale_keys = [
                    str(row["encounter_key"])
                    for row in stale_rows
                    if str(row["encounter_key"]) not in current_keys
                    and int(row["his_recno"]) not in unreadable
                ]
                connection.executemany(
                    "DELETE FROM encounter_state WHERE encounter_key = ?",
                    ((key,) for key in stale_keys),
                )

            for room_id in (1, 2):
                self._normalize_room_positions(connection, room_id, timestamp)

    def assign_room(self, encounter_key: str, room_id: int) -> dict[str, Any]:
        """Set a staff room override and append a present patient to that room's tail."""
        if room_id not in (1, 2):
            raise QueueError("room_id must be 1 or 2.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM encounter_state WHERE encounter_key = ?",
                (encounter_key,),
            ).fetchone()
            if row is None:
                raise QueueError(f"Encounter not found: {encounter_key}")
            if str(row["queue_status"]) in ("COMPLETED", "INVALIDATED"):
                raise QueueError("Completed or invalidated encounters cannot be assigned.")
            position = (
                self._new_room_position(connection, room_id)
                if row["presence_status"] == PresenceState.PRESENT.value
                else None
            )
            connection.execute(
                """
                UPDATE encounter_state
                SET room_id = ?, room_override = 1, queue_position = ?, updated_at = ?
                WHERE encounter_key = ?
                """,
                (room_id, position, self._now(), encounter_key),
            )
            timestamp = self._now()
            old_room_id = row["room_id"]
            if old_room_id in (1, 2):
                self._normalize_room_positions(connection, int(old_room_id), timestamp)
            self._normalize_room_positions(connection, room_id, timestamp)
            updated = connection.execute(
                "SELECT * FROM encounter_state WHERE encounter_key = ?",
                (encounter_key,),
            ).fetchone()
            self._log_action(
                connection,
                encounter_key,
                "assign_room",
                row["room_id"],
                room_id,
            )
            logger.info("Assigned encounter %s to Room %s", encounter_key, room_id)
            return self._row_to_dict(updated)

    @staticmethod
    def _require_active_row(connection: sqlite3.Connection, encounter_key: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM encounter_state WHERE encounter_key = ?",
            (encounter_key,),
        ).fetchone()
        if row is None:
            raise QueueError(f"Encounter not found: {encounter_key}")
        if str(row["queue_status"]) in ("COMPLETED", "INVALIDATED"):
            raise QueueError("Completed or invalidated encounters cannot be changed.")
        return row

    @staticmethod
    def _log_action(
        connection: sqlite3.Connection,
        encounter_key: str,
        action: str,
        old_value: Any,
        new_value: Any,
    ) -> None:
        connection.execute(
            """
            INSERT INTO action_log (timestamp, encounter_key, action, old_value, new_value)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                QueueStore._now(),
                encounter_key,
                action,
                json.dumps(old_value, ensure_ascii=False),
                json.dumps(new_value, ensure_ascii=False),
            ),
        )

    def set_presence(self, encounter_key: str, presence: PresenceState) -> dict[str, Any]:
        """Apply a persistent staff presence decision in server acceptance order."""
        if not isinstance(presence, PresenceState):
            raise QueueError("presence must be PRESENT or AWAY.")
        timestamp = self._now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_active_row(connection, encounter_key)
            old_presence = str(row["presence_status"])
            room_id = row["room_id"]
            if presence is PresenceState.AWAY:
                position = None
            elif old_presence == PresenceState.AWAY.value and room_id is not None:
                position = self._new_room_position(connection, int(room_id))
            else:
                position = row["queue_position"]
            queue_status = str(row["queue_status"])
            current_flag = int(row["current_flag"])
            if presence is PresenceState.AWAY:
                if queue_status in ("CALLED", "IN_CONSULTATION"):
                    queue_status = "WAITING"
                current_flag = 0
            connection.execute(
                """
                UPDATE encounter_state
                SET presence_status = ?, presence_override = 1, queue_position = ?,
                    queue_status = ?, current_flag = ?, updated_at = ?
                WHERE encounter_key = ?
                """,
                (presence.value, position, queue_status, current_flag, timestamp, encounter_key),
            )
            if room_id in (1, 2):
                self._normalize_room_positions(connection, int(room_id), timestamp)
            self._log_action(connection, encounter_key, "presence", old_presence, presence.value)
            logger.info("Set encounter %s presence to %s", encounter_key, presence.value)
            updated = connection.execute(
                "SELECT * FROM encounter_state WHERE encounter_key = ?",
                (encounter_key,),
            ).fetchone()
            return self._row_to_dict(updated)

    def set_overdue(self, encounter_key: str, overdue: bool) -> dict[str, Any]:
        """Set or clear the manual overdue flag."""
        if type(overdue) is not bool:
            raise QueueError("overdue must be true or false.")
        timestamp = self._now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_active_row(connection, encounter_key)
            old_value = bool(row["overdue"])
            connection.execute(
                "UPDATE encounter_state SET overdue = ?, updated_at = ? WHERE encounter_key = ?",
                (int(overdue), timestamp, encounter_key),
            )
            self._log_action(connection, encounter_key, "overdue", old_value, overdue)
            logger.info("Set encounter %s overdue=%s", encounter_key, overdue)
            updated = connection.execute(
                "SELECT * FROM encounter_state WHERE encounter_key = ?",
                (encounter_key,),
            ).fetchone()
            return self._row_to_dict(updated)

    @staticmethod
    def _ordered_room_keys(connection: sqlite3.Connection, room_id: int) -> list[str]:
        rows = connection.execute(
            """
            SELECT encounter_key FROM encounter_state
            WHERE room_id = ? AND presence_status = 'PRESENT'
              AND queue_status NOT IN ('COMPLETED', 'INVALIDATED')
            ORDER BY queue_position, created_at, encounter_key
            """,
            (room_id,),
        ).fetchall()
        return [str(row["encounter_key"]) for row in rows]

    def _reorder_in_transaction(
        self,
        connection: sqlite3.Connection,
        encounter_key: str,
        position: int,
        timestamp: str,
    ) -> dict[str, Any]:
        row = self._require_active_row(connection, encounter_key)
        if row["room_id"] is None or row["presence_status"] != PresenceState.PRESENT.value:
            raise QueueError("Only present, assigned encounters can be reordered.")
        if type(position) is not int or position < 1:
            raise QueueError("position must be a positive integer.")
        keys = self._ordered_room_keys(connection, int(row["room_id"]))
        old_position = keys.index(encounter_key) + 1
        keys.remove(encounter_key)
        keys.insert(min(position - 1, len(keys)), encounter_key)
        for new_position, key in enumerate(keys, start=1):
            if key == encounter_key:
                connection.execute(
                    """
                    UPDATE encounter_state
                    SET queue_position = ?, updated_at = ?,
                        manually_reordered_at = ?, new_highlight_until = NULL
                    WHERE encounter_key = ?
                    """,
                    (new_position, timestamp, timestamp, key),
                )
            else:
                connection.execute(
                    "UPDATE encounter_state SET queue_position = ?, updated_at = ? WHERE encounter_key = ?",
                    (new_position, timestamp, key),
                )
        self._log_action(
            connection,
            encounter_key,
            "reorder",
            old_position,
            keys.index(encounter_key) + 1,
        )
        logger.info(
            "Reordered encounter %s in Room %s from %s to %s",
            encounter_key,
            row["room_id"],
            old_position,
            keys.index(encounter_key) + 1,
        )
        updated = connection.execute(
            "SELECT * FROM encounter_state WHERE encounter_key = ?",
            (encounter_key,),
        ).fetchone()
        return self._row_to_dict(updated)

    def reorder(self, encounter_key: str, position: int) -> dict[str, Any]:
        """Move an encounter to a one-based room position (drag/drop target)."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._reorder_in_transaction(connection, encounter_key, position, self._now())

    def _move(self, encounter_key: str, delta: int) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_active_row(connection, encounter_key)
            if row["room_id"] is None or row["presence_status"] != PresenceState.PRESENT.value:
                raise QueueError("Only present, assigned encounters can be reordered.")
            keys = self._ordered_room_keys(connection, int(row["room_id"]))
            current = keys.index(encounter_key) + 1
            return self._reorder_in_transaction(
                connection,
                encounter_key,
                max(1, min(len(keys), current + delta)),
                self._now(),
            )

    def move_up(self, encounter_key: str) -> dict[str, Any]:
        return self._move(encounter_key, -1)

    def move_down(self, encounter_key: str) -> dict[str, Any]:
        return self._move(encounter_key, 1)

    def _set_current(self, connection: sqlite3.Connection, row: sqlite3.Row, status: str) -> dict[str, Any]:
        encounter_key = str(row["encounter_key"])
        room_id = row["room_id"]
        if room_id is None or row["presence_status"] != PresenceState.PRESENT.value:
            raise QueueError("Only present, assigned encounters can be called current.")
        timestamp = self._now()
        connection.execute(
            "UPDATE encounter_state SET current_flag = 0, updated_at = ? WHERE room_id = ?",
            (timestamp, room_id),
        )
        connection.execute(
            """
            UPDATE encounter_state
            SET queue_status = ?, current_flag = 1, updated_at = ?
            WHERE encounter_key = ?
            """,
            (status, timestamp, encounter_key),
        )
        self._log_action(connection, encounter_key, "queue_status", row["queue_status"], status)
        logger.info("Set encounter %s queue status to %s", encounter_key, status)
        updated = connection.execute(
            "SELECT * FROM encounter_state WHERE encounter_key = ?",
            (encounter_key,),
        ).fetchone()
        return self._row_to_dict(updated)

    def call(self, encounter_key: str) -> dict[str, Any]:
        """Call an encounter and make it the room's current patient."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_active_row(connection, encounter_key)
            return self._set_current(connection, row, "CALLED")

    def set_current(self, encounter_key: str) -> dict[str, Any]:
        """Mark an encounter as being in consultation."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_active_row(connection, encounter_key)
            return self._set_current(connection, row, "IN_CONSULTATION")

    def next_patient(self, room_id: int) -> dict[str, Any]:
        """Call the first waiting patient in the selected room."""
        if room_id not in (1, 2):
            raise QueueError("room_id must be 1 or 2.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM encounter_state
                WHERE room_id = ? AND presence_status = 'PRESENT' AND queue_status = 'WAITING'
                ORDER BY queue_position, created_at, encounter_key
                LIMIT 1
                """,
                (room_id,),
            ).fetchone()
            if row is None:
                raise QueueError(f"No waiting encounter in Room {room_id}.")
            return self._set_current(connection, row, "CALLED")

    def remove_encounter(self, encounter_key: str) -> None:
        """Remove an encounter that no longer belongs to the clinic date."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT room_id FROM encounter_state WHERE encounter_key = ?",
                (encounter_key,),
            ).fetchone()
            connection.execute("DELETE FROM encounter_state WHERE encounter_key = ?", (encounter_key,))
            if row is not None and row["room_id"] in (1, 2):
                self._normalize_room_positions(connection, int(row["room_id"]), self._now())

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        entry = dict(row)
        entry["raw_his"] = json.loads(str(entry.pop("raw_his_json")))
        entry["queue_position"] = (
            None if entry["queue_position"] is None else int(entry["queue_position"])
        )
        entry["room_override"] = bool(entry["room_override"])
        entry["presence_override"] = bool(entry["presence_override"])
        entry["overdue"] = bool(entry["overdue"])
        entry["current_flag"] = bool(entry["current_flag"])
        highlight_until = entry["new_highlight_until"]
        try:
            expires = datetime.fromisoformat(str(highlight_until)) if highlight_until else None
            if expires is not None and expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            entry["is_new"] = bool(expires and expires > datetime.now(timezone.utc))
        except ValueError:
            entry["is_new"] = False
        return entry

    def get_board(self) -> dict[str, Any]:
        """Return independently ordered room queues and all unassigned encounters."""
        board: dict[str, Any] = {
            "rooms": {
                1: {"waiting": [], "away": [], "other": []},
                2: {"waiting": [], "away": [], "other": []},
            },
            "unassigned": [],
            "completed": [],
            "invalidated": [],
        }
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM encounter_state
                ORDER BY CASE WHEN room_id IS NULL THEN 3 ELSE room_id END,
                         CASE WHEN queue_position IS NULL THEN 1 ELSE 0 END,
                         queue_position, created_at, encounter_key
                """
            ).fetchall()

        for row in rows:
            entry = self._row_to_dict(row)
            if entry["queue_status"] == "COMPLETED":
                board["completed"].append(entry)
            elif entry["queue_status"] == "INVALIDATED":
                board["invalidated"].append(entry)
            elif entry["room_id"] is None:
                board["unassigned"].append(entry)
            elif entry["presence_status"] == PresenceState.AWAY.value:
                board["rooms"][entry["room_id"]]["away"].append(entry)
            elif entry["queue_status"] == "WAITING":
                board["rooms"][entry["room_id"]]["waiting"].append(entry)
            else:
                board["rooms"][entry["room_id"]]["other"].append(entry)
        return board
