"""Read-only polling and projection for the experimental VISIT source."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging
from threading import RLock
from typing import Callable, Iterable, Mapping

from .config import AppConfig
from .dbf import DBFHeader, DBFReadError, DBFReader, DBFRecord
from .his import EncounterSnapshot, parse_his_date
from .state import HISState, normalize_time_kind


logger = logging.getLogger("clinic_queue.visit")


@dataclass
class _PendingRecord:
    record: DBFRecord
    consecutive_reads: int


@dataclass
class _PendingBaseline:
    identity: tuple[int, int]
    layout: tuple[object, ...]
    records: tuple[DBFRecord, ...]


@dataclass(frozen=True)
class _Association:
    encounter: EncounterSnapshot | None
    reason: str


class VisitMonitor:
    """Observe stable VISIT records without changing queue-owned state.

    Records visible at startup are a baseline. A record that is new or differs
    from the last accepted bytes must be read identically twice before it is
    considered evidence. Raw records are kept only in memory so this monitor
    cannot turn the VISIT source into a second application database.
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        encounter_provider: Callable[[], Iterable[EncounterSnapshot]] | None = None,
        room_map_provider: Callable[[], Mapping[str, int]] | None = None,
    ) -> None:
        self.config = config
        self.enabled = config.visit_monitor_enabled
        self.source_path = config.his_data_path / config.rg011m1_visit_filename
        self.source_status = "DISABLED" if not self.enabled else "ERROR"
        self.source_error: str | None = None
        self._lock = RLock()
        self.baseline_established = False
        self.baseline_record_count = 0
        self.source_record_count = 0
        self._baseline_records: dict[int, DBFRecord] = {}
        self._accepted_records: dict[int, DBFRecord] = {}
        self._pending_records: dict[int, _PendingRecord] = {}
        self._unreadable_records: dict[int, str] = {}
        self._diagnostics: dict[int, str] = {}
        self._header_layout: tuple[object, ...] | None = None
        self._source_identity: tuple[int, int] | None = None
        self._pending_baseline: _PendingBaseline | None = None
        self._encounter_provider = encounter_provider
        self._room_map_provider = room_map_provider
        self._encounters: tuple[EncounterSnapshot, ...] = ()
        self._encounters_snapshot_set = False

    def set_encounters(self, encounters: Iterable[EncounterSnapshot]) -> None:
        """Set the current-day RG encounter view used for association."""
        with self._lock:
            self._encounters = tuple(encounters)
            self._encounters_snapshot_set = True

    def initialize(self) -> None:
        """Read all visible rows as baseline data, never as events."""
        if not self.enabled:
            return

        with self._lock:
            self._initialize()

    def _initialize(self) -> None:
        try:
            header, records, errors, identity = self._read_source()
            if errors:
                raise DBFReadError(
                    "VISIT source contains unreadable records: "
                    + ", ".join(map(str, errors))
                )
        except (DBFReadError, OSError) as exc:
            self._mark_source_failure(exc)
            return

        self._baseline_records = {record.recno: record for record in records}
        self._accepted_records.clear()
        self._pending_records.clear()
        self._unreadable_records.clear()
        self._diagnostics.clear()
        self._header_layout = self._layout(header)
        self._source_identity = identity
        self._pending_baseline = None
        self.baseline_record_count = header.record_count
        self.source_record_count = header.record_count
        self.baseline_established = True
        self.source_status = "OK"
        self.source_error = None
        logger.info(
            "Established VISIT monitor baseline from %s records in %s",
            header.record_count,
            self.source_path,
        )

    @staticmethod
    def _layout(header: DBFHeader) -> tuple[object, ...]:
        return (header.header_length, header.record_length, header.fields)

    def _read_source(self) -> tuple[DBFHeader, list[DBFRecord], list[int], tuple[int, int]]:
        reader = DBFReader(self.source_path)
        header = reader.read_header()
        errors: list[int] = []
        records = list(
            reader.iter_records(
                end=header.record_count,
                header=header,
                record_errors=errors,
            )
        )
        stat = self.source_path.stat()
        identity = (int(stat.st_dev), int(stat.st_ino or stat.st_ctime_ns))
        return header, records, errors, identity

    @staticmethod
    def _same_baseline(left: _PendingBaseline, right: _PendingBaseline) -> bool:
        return (
            left.identity == right.identity
            and left.layout == right.layout
            and tuple((record.recno, record.raw_bytes) for record in left.records)
            == tuple((record.recno, record.raw_bytes) for record in right.records)
        )

    def _provided_encounters(self) -> tuple[EncounterSnapshot, ...]:
        if self._encounters_snapshot_set or self._encounter_provider is None:
            return self._encounters
        try:
            return tuple(self._encounter_provider())
        except Exception as exc:  # pragma: no cover - defensive provider boundary
            logger.warning("VISIT encounter association is unavailable: %s", exc)
            return ()

    def _room_map(self) -> Mapping[str, int]:
        if self._room_map_provider is None:
            return {}
        try:
            return dict(self._room_map_provider())
        except Exception as exc:  # pragma: no cover - defensive provider boundary
            logger.warning("VISIT room mapping is unavailable: %s", exc)
            return {}

    def poll_once(self) -> None:
        """Accept only stable changed records and refresh read-only evidence."""
        if not self.enabled:
            return

        with self._lock:
            self._poll_once()

    def _poll_once(self) -> None:
        try:
            header, records, errors, identity = self._read_source()
        except (DBFReadError, OSError) as exc:
            self._mark_source_failure(exc)
            return

        layout = self._layout(header)
        reset_required = (
            not self.baseline_established
            or self._header_layout != layout
            or self._source_identity != identity
            or header.record_count < self.baseline_record_count
        )
        if reset_required:
            # A layout/replacement/shrink invalidates physical offsets. Require
            # two identical complete source reads before accepting a new
            # baseline, so reused record numbers cannot replay as events.
            if errors:
                self._mark_source_failure(
                    DBFReadError(
                        "VISIT source contains unreadable records: "
                        + ", ".join(map(str, errors))
                    )
                )
                return
            snapshot = _PendingBaseline(identity, layout, tuple(records))
            if self._pending_baseline is None or not self._same_baseline(
                self._pending_baseline, snapshot
            ):
                self._pending_baseline = snapshot
                self._mark_source_failure(
                    DBFReadError("VISIT source changed; waiting for a stable baseline read")
                )
                return

            self._baseline_records = {record.recno: record for record in records}
            self._accepted_records.clear()
            self._pending_records.clear()
            self._unreadable_records.clear()
            self._diagnostics.clear()
            self._header_layout = layout
            self._source_identity = identity
            self._pending_baseline = None
            self.baseline_record_count = header.record_count
            self.source_record_count = header.record_count
            self.baseline_established = True
            self.source_status = "OK"
            self.source_error = None
            return

        self._pending_baseline = None
        self.source_record_count = header.record_count
        self._unreadable_records = {
            recno: "record is incomplete or malformed; waiting for a complete read"
            for recno in errors
        }
        if errors:
            self._mark_source_failure(
                DBFReadError(
                    "VISIT source contains unreadable records: "
                    + ", ".join(map(str, errors))
                )
            )
            return
        self.source_status = "OK"
        self.source_error = None

        records_by_recno = {record.recno: record for record in records}
        for recno, record in records_by_recno.items():
            baseline = self._baseline_records.get(recno)
            accepted = self._accepted_records.get(recno)
            if accepted is not None and accepted.raw_bytes == record.raw_bytes:
                self._pending_records.pop(recno, None)
                continue
            if accepted is None and baseline is not None and baseline.raw_bytes == record.raw_bytes:
                self._pending_records.pop(recno, None)
                continue

            pending = self._pending_records.get(recno)
            if pending is None or pending.record.raw_bytes != record.raw_bytes:
                self._pending_records[recno] = _PendingRecord(record, 1)
                continue

            pending.consecutive_reads += 1
            if pending.consecutive_reads < 2:
                continue
            self._accepted_records[recno] = record
            self._pending_records.pop(recno, None)
            self._unreadable_records.pop(recno, None)

    def _associate(self, record: DBFRecord) -> _Association:
        visit_date = parse_his_date(record.raw_value("SDATE"))
        if visit_date is None:
            return _Association(None, "VISIT SDATE is missing or invalid")
        if visit_date != date.today():
            return _Association(None, "VISIT SDATE is not the current clinic date")

        time_kind = normalize_time_kind(record.value("TIME_KIND"))
        if time_kind is None:
            return _Association(None, "TIME_KIND is missing or unsupported")

        patient_no = record.value("NUM")
        doctor_code = record.value("CCDOC")
        encounters = self._provided_encounters()
        sys_2015 = record.value("SYS_2015")
        if sys_2015:
            exact = [
                encounter
                for encounter in encounters
                if encounter.raw_his.get("SYS_2015", "").strip() == sys_2015
            ]
            if len(exact) == 1:
                return _Association(exact[0], "exact SYS_2015 association")

        if not patient_no or not doctor_code:
            return _Association(None, "NUM, CCDOC, date, and TIME_KIND are required for fallback")

        composite = [
            encounter
            for encounter in encounters
            if encounter.patient_no == patient_no
            and encounter.doctor_code == doctor_code
            and encounter.visit_date == date.today()
            and normalize_time_kind(encounter.raw_his.get("TIME_KIND")) == time_kind
        ]
        if len(composite) == 1:
            return _Association(composite[0], "unique NUM+CCDOC+date+TIME_KIND fallback")
        if len(composite) > 1:
            return _Association(None, "composite encounter association is ambiguous")
        if sys_2015:
            return _Association(None, "SYS_2015 did not match a current-day encounter")
        return _Association(None, "no unique NUM+CCDOC+date+TIME_KIND encounter match")

    def _candidate_for_record(self, record: DBFRecord) -> dict | None:
        if record.deleted:
            self._diagnostics[record.recno] = "VISIT evidence is logically deleted"
            return None
        association = self._associate(record)
        encounter = association.encounter
        if encounter is None:
            self._diagnostics[record.recno] = association.reason
            return None
        if encounter.his_state is HISState.COMPLETED:
            self._diagnostics[record.recno] = (
                "associated encounter is already HIS-derived COMPLETED"
            )
            return None
        self._diagnostics.pop(record.recno, None)

        time_kind = normalize_time_kind(record.value("TIME_KIND"))
        assert time_kind is not None
        doctor_code = record.value("CCDOC")
        return {
            "status": "UNKNOWN",
            "encounter_key": encounter.encounter_key,
            "patient_no": encounter.patient_no,
            "patient_name": encounter.patient_name,
            "doctor_code": doctor_code,
            "time_kind": int(time_kind),
            "clinic_session": time_kind.label,
            "evidence_reason": association.reason,
            "evidence_count": 1,
            "record_locators": [
                {
                    "source": self.config.rg011m1_visit_filename,
                    "record_number": record.recno,
                }
            ],
        }

    def _mark_source_failure(self, error: Exception) -> None:
        self._pending_records.clear()
        has_last_observation = self.baseline_established
        self.source_status = "STALE" if has_last_observation else "ERROR"
        self.source_error = str(error)
        if not has_last_observation:
            self.baseline_established = False
            self.baseline_record_count = 0
            self.source_record_count = 0
            self._baseline_records.clear()
            self._accepted_records.clear()
            self._pending_records.clear()
            self._unreadable_records.clear()
            self._diagnostics.clear()
            self._header_layout = None
            self._source_identity = None
        logger.warning("VISIT monitor source is unavailable: %s", error)

    def response(self, *, selected_time_kind: int | None = None) -> dict:
        """Return a JSON-safe, read-only projection for the API and template."""
        with self._lock:
            return self._response(selected_time_kind=selected_time_kind)

    def _response(self, *, selected_time_kind: int | None = None) -> dict:
        if not self.enabled:
            status = "DISABLED"
            room_candidates = {1: [], 2: []}
            unmatched_candidates: list[dict] = []
        elif self.source_status == "ERROR":
            status = "ERROR"
            room_candidates = {1: [], 2: []}
            unmatched_candidates = []
        else:
            status = "STALE" if self.source_status == "STALE" else "NONE"
            room_candidates = {1: [], 2: []}
            unmatched_candidates = []
            time_filter = normalize_time_kind(selected_time_kind)
            room_map = self._room_map()
            candidates_by_identity: dict[tuple[str, str, int, str], dict] = {}
            for record in sorted(self._accepted_records.values(), key=lambda item: item.recno):
                candidate = self._candidate_for_record(record)
                if candidate is None:
                    continue
                identity = (
                    candidate["encounter_key"],
                    candidate["doctor_code"],
                    candidate["time_kind"],
                    candidate["patient_no"],
                )
                existing = candidates_by_identity.get(identity)
                if existing is None:
                    candidates_by_identity[identity] = candidate
                else:
                    existing["record_locators"].extend(candidate["record_locators"])
                    existing["evidence_count"] += candidate["evidence_count"]

            candidates_by_physician_session: dict[tuple[str, int], list[dict]] = {}
            for candidate in candidates_by_identity.values():
                group_key = (candidate["doctor_code"], candidate["time_kind"])
                candidates_by_physician_session.setdefault(group_key, []).append(candidate)

            for group in candidates_by_physician_session.values():
                candidate_status = "UNKNOWN" if len(group) == 1 else "AMBIGUOUS"
                for candidate in group:
                    candidate["status"] = candidate_status

            projected_candidates = sorted(
                candidates_by_identity.values(),
                key=lambda candidate: (
                    candidate["doctor_code"],
                    candidate["time_kind"],
                    candidate["encounter_key"],
                    candidate["patient_no"],
                ),
            )
            for candidate in projected_candidates:
                room_id = room_map.get(candidate["doctor_code"])
                if not candidate["doctor_code"]:
                    placement_reason = (
                        "CCDOC is blank; candidate remains in read-only unmatched diagnostics"
                    )
                else:
                    placement_reason = (
                        f"CCDOC {candidate['doctor_code']!r} does not match an active room filter; "
                        "candidate remains in read-only unmatched diagnostics"
                    )
                for locator in candidate["record_locators"]:
                    record_number = int(locator["record_number"])
                    if room_id in room_candidates:
                        self._diagnostics.pop(record_number, None)
                    else:
                        self._diagnostics[record_number] = placement_reason
                if time_filter is not None and candidate["time_kind"] != int(time_filter):
                    continue
                if room_id in room_candidates:
                    room_candidates[room_id].append(candidate)
                else:
                    unmatched_candidates.append(candidate)
            visible_candidates = [
                candidate
                for candidate in projected_candidates
                if time_filter is None or candidate["time_kind"] == int(time_filter)
            ]
            if visible_candidates and status != "STALE":
                status = (
                    "AMBIGUOUS"
                    if any(candidate["status"] == "AMBIGUOUS" for candidate in visible_candidates)
                    else "UNKNOWN"
                )

        room_state = {}
        for room_id, candidates in room_candidates.items():
            if status == "STALE":
                room_status = "STALE"
            elif candidates:
                room_status = (
                    "AMBIGUOUS"
                    if any(candidate["status"] == "AMBIGUOUS" for candidate in candidates)
                    else "UNKNOWN"
                )
            elif status in {"DISABLED", "ERROR", "STALE"}:
                room_status = status
            else:
                room_status = "NONE"
            room_state[room_id] = {
                "status": room_status,
                "candidates": candidates,
            }
        if status == "STALE":
            unmatched_status = "STALE"
        elif unmatched_candidates:
            unmatched_status = (
                "AMBIGUOUS"
                if any(candidate["status"] == "AMBIGUOUS" for candidate in unmatched_candidates)
                else "UNKNOWN"
            )
        elif status in {"DISABLED", "ERROR", "STALE"}:
            unmatched_status = status
        else:
            unmatched_status = "NONE"
        source_health = {
            "status": self.source_status,
            "filename": self.config.rg011m1_visit_filename,
            "baseline_established": self.baseline_established,
            "record_count": self.source_record_count,
            "error": self.source_error,
        }
        pending = [
            {
                "record_number": recno,
                "consecutive_reads": pending_record.consecutive_reads,
                "reason": "awaiting a second identical complete read",
            }
            for recno, pending_record in sorted(self._pending_records.items())
        ]
        pending.extend(
            {
                "record_number": recno,
                "consecutive_reads": 0,
                "reason": reason,
            }
            for recno, reason in sorted(self._unreadable_records.items())
        )
        diagnostics = [
            {"record_number": recno, "reason": reason}
            for recno, reason in sorted(self._diagnostics.items())
        ]
        result = {
            "enabled": self.enabled,
            "status": status,
            "source_health": source_health,
            "baseline": {
                "established": self.baseline_established,
                "record_count": self.baseline_record_count,
            },
            "rooms": room_state,
            "unmatched": {
                "status": unmatched_status,
                "candidates": unmatched_candidates,
            },
            "pending": pending,
            "diagnostics": diagnostics,
        }
        if selected_time_kind is not None:
            result["selected_time_kind"] = selected_time_kind
        return result
