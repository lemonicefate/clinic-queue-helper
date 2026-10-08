"""SQLite persistence and routing for application-owned encounter state."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .his import EncounterSnapshot
from .state import (
    ClinicSession,
    HISState,
    PresenceState,
    normalize_time_kind,
    resolve_his_state,
)


logger = logging.getLogger("clinic_queue.queue")


@dataclass(frozen=True)
class QueueScope:
    room_id: int
    time_kind: ClinicSession


_QUEUE_STATUS_BY_HIS_STATE = {
    HISState.INVALID: "INVALIDATED",
    HISState.COMPLETED: "COMPLETED",
    HISState.PREREGISTERED: "PREREGISTERED",
    HISState.WAITING: "WAITING",
}


def _queue_status(his_state: HISState) -> str:
    return _QUEUE_STATUS_BY_HIS_STATE[his_state]


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
                    time_kind INTEGER CHECK (time_kind IN (1, 2, 3) OR time_kind IS NULL),
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
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(encounter_state)").fetchall()
            }
            if "time_kind" not in columns:
                connection.execute("ALTER TABLE encounter_state ADD COLUMN time_kind INTEGER")
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS room_doctor_filter (
                    room_id INTEGER PRIMARY KEY CHECK (room_id IN (1, 2)),
                    doctor_code TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._migrate_encounter_state(connection)

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
    def _room_doctor_codes(connection: sqlite3.Connection) -> dict[int, str]:
        codes = {1: "", 2: ""}
        rows = connection.execute(
            "SELECT room_id, doctor_code FROM room_doctor_filter"
        ).fetchall()
        for row in rows:
            codes[int(row["room_id"])] = str(row["doctor_code"])
        return codes

    @staticmethod
    def _doctor_room_map(codes: Mapping[int, str]) -> dict[str, int]:
        return {
            code: room_id
            for room_id, raw_code in codes.items()
            if (code := str(raw_code).strip(" \x00"))
        }

    def initialize_room_doctor_codes(self, legacy_map: Mapping[str, int]) -> None:
        """Seed shared room filters once from the previous static configuration."""
        legacy_by_room: dict[int, list[str]] = {1: [], 2: []}
        for raw_code, room_id in legacy_map.items():
            code = str(raw_code).strip(" \x00")
            if code and room_id in legacy_by_room:
                legacy_by_room[room_id].append(code)

        timestamp = self._now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for room_id in (1, 2):
                exists = connection.execute(
                    "SELECT 1 FROM room_doctor_filter WHERE room_id = ?",
                    (room_id,),
                ).fetchone()
                if exists is not None:
                    continue
                candidates = legacy_by_room[room_id]
                code = candidates[0] if len(candidates) == 1 else ""
                if len(candidates) > 1:
                    logger.warning(
                        "Legacy configuration has multiple physician codes for room %s; "
                        "leave its shared filter blank for manual entry",
                        room_id,
                    )
                connection.execute(
                    "INSERT INTO room_doctor_filter (room_id, doctor_code) VALUES (?, ?)",
                    (room_id, code),
                )
            codes = self._room_doctor_codes(connection)
            self._remap_room_assignments(
                connection,
                self._doctor_room_map(codes),
                timestamp,
            )

    def get_room_doctor_codes(self) -> dict[int, str]:
        with self._connection() as connection:
            return self._room_doctor_codes(connection)

    def get_doctor_room_map(self) -> dict[str, int]:
        return self._doctor_room_map(self.get_room_doctor_codes())

    def set_room_doctor_code(self, room_id: int, doctor_code: str) -> dict[int, str]:
        """Save one shared room filter and move records to their CCDOC-derived room."""
        if type(room_id) is not int or room_id not in (1, 2):
            raise QueueError("room_id must be 1 or 2.")
        if not isinstance(doctor_code, str):
            raise QueueError("doctor_code must be a string.")
        code = doctor_code.strip(" \x00")
        timestamp = self._now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            other_room = 2 if room_id == 1 else 1
            other = connection.execute(
                "SELECT doctor_code FROM room_doctor_filter WHERE room_id = ?",
                (other_room,),
            ).fetchone()
            if code and other is not None and str(other["doctor_code"]) == code:
                raise QueueError("This physician code is already selected for the other room.")

            connection.execute(
                """
                INSERT INTO room_doctor_filter (room_id, doctor_code) VALUES (?, ?)
                ON CONFLICT(room_id) DO UPDATE SET doctor_code = excluded.doctor_code
                """,
                (room_id, code),
            )
            codes = self._room_doctor_codes(connection)
            self._remap_room_assignments(
                connection,
                self._doctor_room_map(codes),
                timestamp,
            )
            return codes

    @staticmethod
    def _remap_room_assignments(
        connection: sqlite3.Connection,
        doctor_room_map: Mapping[str, int],
        timestamp: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT encounter_key, doctor_code, room_id, time_kind, presence_status,
                   queue_status, queue_position
            FROM encounter_state
            ORDER BY CASE WHEN room_id IS NULL THEN 3 ELSE room_id END,
                     CASE WHEN queue_position IS NULL THEN 1 ELSE 0 END,
                     queue_position, created_at, encounter_key
            """
        ).fetchall()
        for row in rows:
            code = str(row["doctor_code"]).strip(" \x00")
            mapped_room = doctor_room_map.get(code)
            if mapped_room not in (1, 2):
                mapped_room = None
            old_room = row["room_id"]
            time_kind = row["time_kind"]
            is_waiting_candidate = (
                mapped_room is not None
                and time_kind is not None
                and row["presence_status"] == PresenceState.PRESENT.value
                and row["queue_status"] == "WAITING"
            )
            if not is_waiting_candidate:
                position = None
            elif old_room == mapped_room and row["queue_position"] is not None:
                position = int(row["queue_position"])
            else:
                assert mapped_room is not None
                position = QueueStore._new_room_position(
                    connection, QueueScope(mapped_room, ClinicSession(int(time_kind)))
                )
            connection.execute(
                """
                UPDATE encounter_state
                SET room_id = ?, queue_position = ?, room_override = 0, updated_at = ?
                WHERE encounter_key = ?
                """,
                (mapped_room, position, timestamp, row["encounter_key"]),
            )
        QueueStore._normalize_all_room_positions(connection, timestamp)

    @staticmethod
    def _migrate_encounter_state(connection: sqlite3.Connection) -> None:
        """Derive session and supported visit categories from HIS data on every startup."""
        timestamp = QueueStore._now()
        rows = connection.execute("SELECT * FROM encounter_state").fetchall()
        for row in rows:
            raw_his = json.loads(str(row["raw_his_json"]))
            resolved = resolve_his_state(raw_his)
            queue_status = _queue_status(resolved.state)
            time_kind = normalize_time_kind(raw_his.get("TIME_KIND"))
            presence = (
                str(row["presence_status"])
                if row["presence_override"]
                else (resolved.initial_presence or PresenceState.PRESENT).value
            )
            position = row["queue_position"]
            if (
                queue_status != "WAITING"
                or row["room_id"] is None
                or presence == PresenceState.AWAY.value
                or time_kind is None
            ):
                position = None
            connection.execute(
                """
                UPDATE encounter_state
                SET time_kind = ?, his_state = ?, queue_status = ?, presence_status = ?,
                    queue_position = ?, room_override = 0,
                    current_flag = 0, updated_at = ?
                WHERE encounter_key = ?
                """,
                (
                    None if time_kind is None else int(time_kind),
                    resolved.state.value,
                    queue_status,
                    presence,
                    position,
                    timestamp,
                    row["encounter_key"],
                ),
            )
        QueueStore._normalize_all_room_positions(connection, timestamp)

    @staticmethod
    def _new_room_position(connection: sqlite3.Connection, scope: QueueScope) -> int:
        result = connection.execute(
            """
            SELECT COALESCE(MAX(queue_position), 0) + 1
            FROM encounter_state
            WHERE room_id = ? AND time_kind = ? AND presence_status = 'PRESENT'
              AND queue_status = 'WAITING'
            """,
            (scope.room_id, int(scope.time_kind)),
        ).fetchone()
        return int(result[0])

    @staticmethod
    def _normalize_room_positions(
        connection: sqlite3.Connection,
        scope: QueueScope,
        timestamp: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT encounter_key, queue_position FROM encounter_state
            WHERE room_id = ? AND time_kind = ? AND presence_status = 'PRESENT'
              AND queue_status = 'WAITING'
            ORDER BY CASE WHEN queue_position IS NULL THEN 1 ELSE 0 END,
                     queue_position, created_at, encounter_key
            """,
            (scope.room_id, int(scope.time_kind)),
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

    @staticmethod
    def _normalize_all_room_positions(connection: sqlite3.Connection, timestamp: str) -> None:
        for room_id in (1, 2):
            time_kinds = connection.execute(
                "SELECT DISTINCT time_kind FROM encounter_state WHERE room_id = ? AND time_kind IS NOT NULL",
                (room_id,),
            ).fetchall()
            for row in time_kinds:
                QueueStore._normalize_room_positions(
                    connection,
                    QueueScope(room_id, ClinicSession(int(row["time_kind"]))),
                    timestamp,
                )
            connection.execute(
                "UPDATE encounter_state SET queue_position = NULL WHERE room_id = ? AND time_kind IS NULL",
                (room_id,),
            )

    def reconcile(
        self,
        encounters: Sequence[EncounterSnapshot],
        doctor_room_map: Mapping[str, int] | None = None,
        *,
        new_highlight_seconds: int = 300,
        clinic_date: date | None = None,
        unreadable_recno: Sequence[int] = (),
    ) -> None:
        """Upsert HIS state using the latest shared room filters and preserve local state."""
        timestamp = self._now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            saved_filters = connection.execute(
                "SELECT room_id, doctor_code FROM room_doctor_filter"
            ).fetchall()
            if saved_filters:
                saved_codes = self._room_doctor_codes(connection)
                doctor_room_map = self._doctor_room_map(saved_codes)
            elif doctor_room_map is None:
                doctor_room_map = {}

            current_keys = {encounter.encounter_key for encounter in encounters}
            for encounter in encounters:
                time_kind = normalize_time_kind(encounter.raw_his.get("TIME_KIND"))
                mapped_room = doctor_room_map.get(encounter.doctor_code)
                if mapped_room not in (1, 2):
                    mapped_room = None
                    logger.debug(
                        "Physician code %r for encounter %s is outside the active room filters",
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
                    scope = (
                        QueueScope(room_id, time_kind)
                        if room_id is not None and time_kind is not None
                        else None
                    )
                    position = (
                        self._new_room_position(connection, scope)
                        if scope is not None and presence is PresenceState.PRESENT
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
                else:
                    room_override = 0
                    presence_override = int(existing["presence_override"])
                    room_id = mapped_room
                    presence = (
                        PresenceState(existing["presence_status"])
                        if presence_override
                        else encounter.initial_presence or PresenceState.PRESENT
                    )
                    old_position = existing["queue_position"]
                    same_room = existing["room_id"] == room_id
                    same_group = same_room and existing["time_kind"] == (
                        None if time_kind is None else int(time_kind)
                    )
                    if (
                        room_id is None
                        or time_kind is None
                        or presence is PresenceState.AWAY
                        or encounter.his_state is not HISState.WAITING
                    ):
                        position = None
                    elif same_group and old_position is not None:
                        position = int(old_position)
                    else:
                        position = self._new_room_position(
                            connection, QueueScope(room_id, time_kind)
                        )
                    created_at = str(existing["created_at"])
                    overdue = int(existing["overdue"])
                    highlight_until = existing["new_highlight_until"]
                    reordered_at = existing["manually_reordered_at"]

                queue_status = _queue_status(encounter.his_state)
                if queue_status != "WAITING":
                    position = None

                connection.execute(
                    """
                    INSERT INTO encounter_state (
                        encounter_key, his_recno, patient_no, patient_name, doctor_code,
                        visit_date, registration_time, his_gino1_raw, queue_number,
                        his_state, raw_his_json, time_kind, room_id, presence_status, queue_status,
                        queue_position, overdue, new_highlight_until, manually_reordered_at,
                        current_flag, room_override, presence_override, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        time_kind = excluded.time_kind,
                        room_id = excluded.room_id,
                        presence_status = excluded.presence_status,
                        queue_status = excluded.queue_status,
                        queue_position = excluded.queue_position,
                        overdue = excluded.overdue,
                        new_highlight_until = excluded.new_highlight_until,
                        manually_reordered_at = excluded.manually_reordered_at,
                        current_flag = 0,
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
                        None if time_kind is None else int(time_kind),
                        room_id,
                        presence.value,
                        queue_status,
                        position,
                        overdue,
                        highlight_until,
                        reordered_at,
                        0,
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

            self._normalize_all_room_positions(connection, timestamp)

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
        if row["room_id"] is None:
            raise QueueError("Encounters outside the physician filters are read-only.")
        return row

    @staticmethod
    def _require_reorder_scope(row: sqlite3.Row) -> QueueScope:
        if (
            row["room_id"] is None
            or row["time_kind"] is None
            or row["presence_status"] != PresenceState.PRESENT.value
            or row["queue_status"] != "WAITING"
        ):
            raise QueueError(
                "Only waiting encounters matched to a room with a known clinic session can be reordered."
            )
        return QueueScope(int(row["room_id"]), ClinicSession(int(row["time_kind"])))

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
            if row["queue_status"] != "WAITING":
                raise QueueError("Presence can only be changed for waiting encounters.")
            old_presence = str(row["presence_status"])
            room_id = row["room_id"]
            time_kind = row["time_kind"]
            scope = (
                QueueScope(int(room_id), ClinicSession(int(time_kind)))
                if room_id is not None and time_kind is not None
                else None
            )
            if presence is PresenceState.AWAY:
                position = None
            elif (
                old_presence == PresenceState.AWAY.value
                and room_id is not None
                and scope is not None
            ):
                position = self._new_room_position(connection, scope)
            else:
                position = row["queue_position"]
            connection.execute(
                """
                UPDATE encounter_state
                SET presence_status = ?, presence_override = 1, queue_position = ?,
                    current_flag = 0, updated_at = ?
                WHERE encounter_key = ?
                """,
                (presence.value, position, timestamp, encounter_key),
            )
            if scope is not None:
                self._normalize_room_positions(connection, scope, timestamp)
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
    def _ordered_room_keys(connection: sqlite3.Connection, scope: QueueScope) -> list[str]:
        rows = connection.execute(
            """
            SELECT encounter_key FROM encounter_state
            WHERE room_id = ? AND time_kind = ? AND presence_status = 'PRESENT'
              AND queue_status = 'WAITING'
            ORDER BY queue_position, created_at, encounter_key
            """,
            (scope.room_id, int(scope.time_kind)),
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
        scope = self._require_reorder_scope(row)
        if type(position) is not int or position < 1:
            raise QueueError("position must be a positive integer.")
        keys = self._ordered_room_keys(connection, scope)
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
            "Reordered encounter %s in Room %s, session %s from %s to %s",
            encounter_key,
            row["room_id"],
            row["time_kind"],
            old_position,
            keys.index(encounter_key) + 1,
        )
        updated = connection.execute(
            "SELECT * FROM encounter_state WHERE encounter_key = ?",
            (encounter_key,),
        ).fetchone()
        return self._row_to_dict(updated)

    def reorder(self, encounter_key: str, position: int) -> dict[str, Any]:
        """Move an encounter to an exact one-based final position in its queue."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._reorder_in_transaction(connection, encounter_key, position, self._now())

    def reorder_relative(
        self,
        encounter_key: str,
        target_encounter_key: str,
        insert_after: bool,
    ) -> dict[str, Any]:
        """Place a waiting encounter before or after another in its room/session queue."""
        if type(insert_after) is not bool:
            raise QueueError("insert_after must be true or false.")
        if encounter_key == target_encounter_key:
            raise QueueError("An encounter cannot be reordered relative to itself.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            source = self._require_active_row(connection, encounter_key)
            scope = self._require_reorder_scope(source)
            target = self._require_active_row(connection, target_encounter_key)
            target_scope = self._require_reorder_scope(target)
            if target_scope != scope:
                raise QueueError("Encounters can only be reordered within the same room and clinic session.")
            keys = self._ordered_room_keys(connection, scope)
            if encounter_key not in keys or target_encounter_key not in keys:
                raise QueueError("Only waiting encounters can be reordered.")
            keys.remove(encounter_key)
            position = keys.index(target_encounter_key) + 1 + int(insert_after)
            return self._reorder_in_transaction(connection, encounter_key, position, self._now())

    def _move(self, encounter_key: str, delta: int) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_active_row(connection, encounter_key)
            scope = self._require_reorder_scope(row)
            keys = self._ordered_room_keys(connection, scope)
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

    def remove_encounter(self, encounter_key: str) -> None:
        """Remove an encounter that no longer belongs to the clinic date."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT room_id, time_kind FROM encounter_state WHERE encounter_key = ?",
                (encounter_key,),
            ).fetchone()
            connection.execute("DELETE FROM encounter_state WHERE encounter_key = ?", (encounter_key,))
            if row is not None and row["room_id"] in (1, 2) and row["time_kind"] is not None:
                self._normalize_room_positions(
                    connection,
                    QueueScope(
                        int(row["room_id"]), ClinicSession(int(row["time_kind"]))
                    ),
                    self._now(),
                )

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        entry = dict(row)
        entry["raw_his"] = json.loads(str(entry.pop("raw_his_json")))
        entry["queue_position"] = (
            None if entry["queue_position"] is None else int(entry["queue_position"])
        )
        entry.pop("room_override", None)
        entry["presence_override"] = bool(entry["presence_override"])
        entry["overdue"] = bool(entry["overdue"])
        entry.pop("current_flag", None)
        entry["clinic_session"] = (
            ClinicSession(int(entry["time_kind"])).label
            if entry["time_kind"] is not None
            else "診別未確認"
        )
        highlight_until = entry["new_highlight_until"]
        try:
            expires = datetime.fromisoformat(str(highlight_until)) if highlight_until else None
            if expires is not None and expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            entry["is_new"] = bool(expires and expires > datetime.now(timezone.utc))
        except ValueError:
            entry["is_new"] = False
        return entry

    def get_board(self, time_kind: ClinicSession | int | None = None) -> dict[str, Any]:
        """Return doctor-filtered room queues and separate diagnostic groups."""
        selected_session = normalize_time_kind(time_kind) if time_kind is not None else None
        if time_kind is not None and selected_session is None:
            raise QueueError("time_kind must be 1, 2, or 3.")

        def empty_rooms() -> dict[int, dict[str, list[dict[str, Any]]]]:
            return {
                1: {"waiting": [], "away": [], "preregistered": []},
                2: {"waiting": [], "away": [], "preregistered": []},
            }

        def empty_unmatched() -> dict[str, list[dict[str, Any]]]:
            return {
                "waiting": [],
                "away": [],
                "preregistered": [],
                "completed": [],
                "invalidated": [],
            }

        board: dict[str, Any] = {
            "selected_time_kind": None if selected_session is None else int(selected_session),
            "room_doctor_codes": {1: "", 2: ""},
            "rooms": empty_rooms(),
            "unmatched": empty_unmatched(),
            "completed": [],
            "invalidated": [],
            "unconfirmed": {
                "rooms": empty_rooms(),
                "unmatched": empty_unmatched(),
                "completed": [],
                "invalidated": [],
            },
        }
        with self._connection() as connection:
            board["room_doctor_codes"] = self._room_doctor_codes(connection)
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
            unknown_session = entry["time_kind"] is None
            if not unknown_session and selected_session is not None:
                if entry["time_kind"] != int(selected_session):
                    continue
            group = board["unconfirmed"] if unknown_session else board
            destination = group["unmatched"] if entry["room_id"] is None else None
            if entry["queue_status"] == "COMPLETED":
                (destination or group)["completed"].append(entry)
            elif entry["queue_status"] == "INVALIDATED":
                (destination or group)["invalidated"].append(entry)
            elif entry["queue_status"] == "PREREGISTERED":
                if destination is not None:
                    destination["preregistered"].append(entry)
                else:
                    group["rooms"][entry["room_id"]]["preregistered"].append(entry)
            elif destination is not None:
                status = (
                    "away"
                    if entry["presence_status"] == PresenceState.AWAY.value
                    else "waiting"
                )
                destination[status].append(entry)
            elif entry["presence_status"] == PresenceState.AWAY.value:
                group["rooms"][entry["room_id"]]["away"].append(entry)
            else:
                group["rooms"][entry["room_id"]]["waiting"].append(entry)
        return board
