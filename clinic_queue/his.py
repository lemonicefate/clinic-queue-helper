"""Read the minimum current-day encounter data needed by the queue."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from .config import AppConfig
from .dbf import DBFHeader, DBFReader, DBFRecord, DBFReadError
from .identity import parse_queue_number, resolve_encounter_key
from .state import HISState, PresenceState, resolve_his_state


logger = logging.getLogger("clinic_queue.his")

RAW_HIS_FIELDS = (
    "NUM",
    "CCDATE",
    "CCTIME",
    "TETDAY",
    "CCDOC",
    "OVER",
    "TREAT",
    "TRDATE",
    "TRTIME",
    "TIME_KIND",
    "GINO1",
    "RELKEY",
    "SYS_2015",
)


@dataclass(frozen=True)
class EncounterSnapshot:
    encounter_key: str
    recno: int
    patient_no: str
    patient_name: str
    doctor_code: str
    visit_date: date
    registration_time: str
    gino1_raw: str
    queue_number: str
    his_state: HISState
    initial_presence: PresenceState | None
    active: bool
    raw_his: dict[str, str]


def parse_his_date(value: Any) -> date | None:
    """Parse DBF date values without assuming CCDATE is the visit date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = "" if value is None else str(value).strip()
    if len(text) == 8 and text.isdigit():
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except ValueError:
            return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _normalized_field(fields: Mapping[str, str], name: str) -> str:
    for key, value in fields.items():
        if key.upper() == name.upper():
            return value.strip(" \x00")
    return ""


class PatientNameLookup:
    """Optional chart-number-to-name index; no other patient fields are retained."""

    def __init__(self, config: AppConfig) -> None:
        self.names: dict[str, str] = {}
        self.lookup_available = False
        self.path = config.his_data_path / config.pd001m1_filename
        if not config.patient_name_field:
            logger.warning(
                "%s patient_name_field is not configured; showing chart numbers", self.path.name
            )
            return

        try:
            reader = DBFReader(self.path)
            header = reader.read_header()
            available = {field.name for field in header.fields}
            required = {config.patient_number_field.upper(), config.patient_name_field.upper()}
            if not required.issubset(available):
                missing = ", ".join(sorted(required - available))
                logger.warning("%s field(s) %s are unavailable; showing chart numbers", self.path.name, missing)
                return
            self.lookup_available = True
            for record in reader.iter_records(
                end=header.record_count,
                field_names=(config.patient_number_field, config.patient_name_field),
            ):
                if record.deleted:
                    continue
                number = _normalized_field(record.raw_fields, config.patient_number_field)
                name = _normalized_field(record.raw_fields, config.patient_name_field)
                if number and name:
                    self.names[number] = name
        except (DBFReadError, OSError) as exc:
            logger.warning("PD001M1 patient-name lookup unavailable at %s: %s", self.path, exc)

    def lookup(self, patient_no: str) -> str:
        name = self.names.get(patient_no)
        if self.lookup_available and not name and patient_no:
            logger.warning("No %s patient name found for chart %s; showing chart number", self.path.name, patient_no)
        return name or patient_no


def snapshot_from_record(
    config: AppConfig,
    record: DBFRecord,
    clinic_date: date,
    patient_names: PatientNameLookup,
) -> EncounterSnapshot | None:
    """Convert one physical RG record into an encounter if it belongs to today."""
    visit_date = parse_his_date(record.raw_value("TETDAY"))
    if visit_date != clinic_date:
        return None
    state_fields: dict[str, Any] = dict(record.raw_fields)
    state_fields["_DELETED"] = record.deleted
    resolved = resolve_his_state(state_fields)

    patient_no = _normalized_field(record.raw_fields, "NUM")
    raw_his = {name: record.raw_value(name) for name in RAW_HIS_FIELDS if name in record.raw_fields}
    raw_his["_DELETED"] = str(record.deleted)
    gino1_raw = record.raw_value("GINO1")
    return EncounterSnapshot(
        encounter_key=resolve_encounter_key(record.raw_fields, recno=record.recno),
        recno=record.recno,
        patient_no=patient_no,
        patient_name=patient_names.lookup(patient_no),
        doctor_code=_normalized_field(record.raw_fields, "CCDOC"),
        visit_date=visit_date,
        registration_time=_normalized_field(record.raw_fields, "CCTIME"),
        gino1_raw=gino1_raw,
        queue_number=parse_queue_number(gino1_raw, patient_no),
        his_state=resolved.state,
        initial_presence=resolved.initial_presence,
        active=resolved.active,
        raw_his=raw_his,
    )


def scan_today(
    config: AppConfig,
    clinic_date: date,
    *,
    include_invalid: bool = False,
    patient_names: PatientNameLookup | None = None,
    header: DBFHeader | None = None,
    record_errors: list[int] | None = None,
) -> list[EncounterSnapshot]:
    """Read records for today's TETDAY, optionally retaining deleted rows for reconciliation."""
    reader = DBFReader(config.his_data_path / config.rg011m1_filename)
    actual_header = header or reader.read_header()
    names = patient_names or PatientNameLookup(config)
    encounters: list[EncounterSnapshot] = []

    for record in reader.iter_records(
        end=actual_header.record_count,
        header=actual_header,
        record_errors=record_errors,
    ):
        encounter = snapshot_from_record(config, record, clinic_date, names)
        if encounter is None:
            continue
        if encounter.his_state is HISState.INVALID and not include_invalid:
            continue
        encounters.append(encounter)
    return encounters
