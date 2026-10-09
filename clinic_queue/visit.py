"""Read-only startup baseline for the experimental VISIT monitor."""

from __future__ import annotations

import logging

from .config import AppConfig
from .dbf import DBFHeader, DBFReadError, DBFReader, DBFRecord


logger = logging.getLogger("clinic_queue.visit")


class VisitMonitor:
    """Own the optional VISIT observation boundary without touching queue state.

    This first slice deliberately only establishes an in-memory baseline. Later
    slices can extend ``poll_once`` with stable-change tracking while keeping
    source health, baseline, and the read-only projection at this boundary.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.enabled = config.visit_monitor_enabled
        self.source_path = config.his_data_path / config.rg011m1_visit_filename
        self.status = "DISABLED" if not self.enabled else "ERROR"
        self.source_status = "DISABLED" if not self.enabled else "ERROR"
        self.source_error: str | None = None
        self.baseline_established = False
        self.baseline_record_count = 0
        self._baseline_records: dict[int, DBFRecord] = {}
        self._header_layout: tuple[object, ...] | None = None

    def initialize(self) -> None:
        """Read every currently visible row as baseline data, never as events."""
        if not self.enabled:
            return

        try:
            reader = DBFReader(self.source_path)
            header = reader.read_header()
            record_errors: list[int] = []
            records = list(
                reader.iter_records(
                    end=header.record_count,
                    header=header,
                    record_errors=record_errors,
                )
            )
            if record_errors:
                raise DBFReadError(
                    f"VISIT source contains unreadable records: {', '.join(map(str, record_errors))}"
                )
        except (DBFReadError, OSError) as exc:
            self._set_error(exc)
            return

        self._baseline_records = {record.recno: record for record in records}
        self._header_layout = self._layout(header)
        self.baseline_record_count = header.record_count
        self.baseline_established = True
        self.source_status = "OK"
        self.source_error = None
        self.status = "NONE"
        logger.info(
            "Established VISIT monitor baseline from %s records in %s",
            header.record_count,
            self.source_path,
        )

    @staticmethod
    def _layout(header: DBFHeader) -> tuple[object, ...]:
        return (header.header_length, header.record_length, header.fields)

    def poll_once(self) -> None:
        """Polling seam reserved for stable VISIT change detection."""

    def _set_error(self, error: Exception) -> None:
        self.status = "ERROR"
        self.source_status = "ERROR"
        self.source_error = str(error)
        self.baseline_established = False
        self.baseline_record_count = 0
        self._baseline_records.clear()
        self._header_layout = None
        logger.warning("VISIT monitor source is unavailable: %s", error)

    def response(self, *, selected_time_kind: int | None = None) -> dict:
        """Return a JSON-safe, read-only projection for the API and template."""
        room_state = {
            "status": self.status,
            "candidates": [],
        }
        source_health = {
            "status": self.source_status,
            "filename": self.config.rg011m1_visit_filename,
            "baseline_established": self.baseline_established,
            "record_count": self.baseline_record_count,
            "error": self.source_error,
        }
        result = {
            "enabled": self.enabled,
            "status": self.status,
            "source_health": source_health,
            "baseline": {
                "established": self.baseline_established,
                "record_count": self.baseline_record_count,
            },
            "rooms": {1: dict(room_state), 2: dict(room_state)},
            "unmatched": {
                "status": self.status,
                "candidates": [],
            },
        }
        if selected_time_kind is not None:
            result["selected_time_kind"] = selected_time_kind
        return result
