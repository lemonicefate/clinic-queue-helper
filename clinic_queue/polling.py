"""Read-only incremental polling and recovery scans for today's HIS records."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from datetime import date
from typing import TYPE_CHECKING

from .config import AppConfig
from .dbf import DBFHeader, DBFReader
from .his import EncounterSnapshot, PatientNameLookup, RAW_HIS_FIELDS, scan_today, snapshot_from_record
from .queue import QueueStore

if TYPE_CHECKING:
    from .visit import VisitMonitor


logger = logging.getLogger("clinic_queue.polling")


class HISPoller:
    """Track today's DBF locators and reconcile only changed rows between recovery scans."""

    def __init__(
        self,
        config: AppConfig,
        store: QueueStore,
        *,
        recovery_interval_seconds: float = 45,
    ) -> None:
        self.config = config
        self.store = store
        self.recovery_interval_seconds = recovery_interval_seconds
        self.tracked_by_recno: dict[int, EncounterSnapshot] = {}
        self._encounter_snapshot: tuple[EncounterSnapshot, ...] = ()
        self.pending_recno: set[int] = set()
        self.last_record_count: int | None = None
        self._header_layout: tuple[object, ...] | None = None
        self._last_full_scan = 0.0
        self._patient_names: PatientNameLookup | None = None
        self.visit_monitor: VisitMonitor | None = None
        self.his_stale = False
        self.his_error: str | None = None
        self.storage_error: str | None = None

    def attach_visit_monitor(self, monitor: VisitMonitor) -> None:
        """Run the optional VISIT observer after each successful HIS poll."""
        self.visit_monitor = monitor

    def encounter_snapshot(self) -> tuple[EncounterSnapshot, ...]:
        """Return the last complete immutable encounter view for VISIT association."""
        return self._encounter_snapshot

    def _poll_visit_monitor(self) -> None:
        if self.visit_monitor is None or not self.visit_monitor.enabled:
            return
        self.visit_monitor.set_encounters(self._encounter_snapshot)
        self.visit_monitor.poll_once()

    def initialize(self) -> None:
        """Perform the initial recovery scan, retaining prior SQLite state on read failure."""
        try:
            self._full_scan()
            self._recovered()
        except sqlite3.Error as exc:
            logger.exception("SQLite queue persistence failed during initial HIS scan")
            self.storage_error = str(exc)
        except OSError as exc:
            self._stale(exc)
        except Exception as exc:
            logger.exception("Initial HIS scan failed")
            self._stale(exc)

    def _reader(self) -> DBFReader:
        return DBFReader(self.config.his_data_path / self.config.rg011m1_filename)

    @staticmethod
    def _layout(header: DBFHeader) -> tuple[object, ...]:
        return (header.header_length, header.record_length, header.fields)

    def _full_scan(self) -> None:
        reader = self._reader()
        header = reader.read_header()
        clinic_date = date.today()
        names = PatientNameLookup(self.config)
        unreadable: list[int] = []
        encounters = scan_today(
            self.config,
            clinic_date,
            include_invalid=True,
            patient_names=names,
            header=header,
            record_errors=unreadable,
        )
        self.store.reconcile(
            encounters,
            new_highlight_seconds=self.config.new_highlight_seconds,
            clinic_date=clinic_date,
            unreadable_recno=unreadable,
        )
        self.tracked_by_recno = {encounter.recno: encounter for encounter in encounters}
        self._encounter_snapshot = tuple(self.tracked_by_recno.values())
        self.pending_recno = set(unreadable)
        self.last_record_count = header.record_count
        self._header_layout = self._layout(header)
        self._patient_names = names
        self._last_full_scan = time.monotonic()
        logger.info(
            "Completed HIS recovery scan for %s: tracked %s of %s records",
            clinic_date,
            len(encounters),
            header.record_count,
        )

    @staticmethod
    def _record_matches_snapshot(record, snapshot: EncounterSnapshot) -> bool:
        if record.recno != snapshot.recno:
            return False
        for name in RAW_HIS_FIELDS:
            if record.raw_value(name) != snapshot.raw_his.get(name, ""):
                return False
        return snapshot.raw_his.get("_DELETED") == str(record.deleted)

    def _poll_incremental(self, header: DBFHeader) -> None:
        assert self.last_record_count is not None
        first_new = self.last_record_count + 1
        recnos = set(self.tracked_by_recno) | self.pending_recno
        if first_new <= header.record_count:
            recnos.update(range(first_new, header.record_count + 1))
        unreadable: list[int] = []
        reader = self._reader()
        names = self._patient_names or PatientNameLookup(self.config)

        for record in reader.iter_selected_records(
            sorted(recnos),
            header,
            record_errors=unreadable,
        ):
            self.pending_recno.discard(record.recno)
            previous = self.tracked_by_recno.get(record.recno)
            if previous is not None and self._record_matches_snapshot(record, previous):
                continue
            encounter = snapshot_from_record(self.config, record, date.today(), names)
            if encounter is None:
                if previous is not None:
                    self.store.remove_encounter(previous.encounter_key)
                    self.tracked_by_recno.pop(record.recno, None)
                continue
            if previous is not None and previous.encounter_key != encounter.encounter_key:
                self.store.remove_encounter(previous.encounter_key)
            self.store.reconcile(
                [encounter],
                new_highlight_seconds=self.config.new_highlight_seconds,
            )
            self.tracked_by_recno[record.recno] = encounter

        if unreadable:
            logger.warning("HIS poll skipped malformed record offsets: %s", unreadable)
            self.pending_recno.update(unreadable)
        self._encounter_snapshot = tuple(self.tracked_by_recno.values())
        self.last_record_count = header.record_count
        self._header_layout = self._layout(header)

    def poll_once(self) -> None:
        """Poll changed offsets or run a periodic full recovery scan; errors are retryable."""
        try:
            now = time.monotonic()
            if (
                self.his_stale
                or
                self.last_record_count is None
                or now - self._last_full_scan >= self.recovery_interval_seconds
            ):
                self._full_scan()
            else:
                header = self._reader().read_header()
                if (
                    header.record_count < self.last_record_count
                    or self._layout(header) != self._header_layout
                ):
                    self._full_scan()
                else:
                    self._poll_incremental(header)
            self._recovered()
            self._poll_visit_monitor()
        except sqlite3.Error as exc:
            logger.exception("SQLite queue persistence failed during HIS polling")
            self.storage_error = str(exc)
        except OSError as exc:
            self._stale(exc)
        except Exception as exc:
            logger.exception("HIS polling failed; retaining the last known queue")
            self._stale(exc)

    def _stale(self, error: Exception) -> None:
        if not self.his_stale:
            logger.warning("HIS synchronization is stale: %s", error)
        self.his_stale = True
        self.his_error = str(error)

    def _recovered(self) -> None:
        if self.his_stale:
            logger.info("HIS synchronization recovered")
        self.his_stale = False
        self.his_error = None
        self.storage_error = None

    async def run(self) -> None:
        """Run indefinitely in the service lifespan without blocking browser requests."""
        interval = self.config.poll_interval_ms / 1000
        while True:
            await asyncio.to_thread(self.poll_once)
            await asyncio.sleep(interval)
