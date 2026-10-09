"""Application configuration loading and validation."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_HIS_DATA_PATH = r"\\192.168.1.199\D\WM003\DATA"
DEFAULT_VISIT_FILENAME = "RG011M1_VISIT.DBF"


class ConfigurationError(ValueError):
    """Raised when the local application configuration cannot be used."""


@dataclass(frozen=True)
class AppConfig:
    bind_host: str
    port: int
    his_data_path: Path
    rg011m1_filename: str
    rg011m1_visit_filename: str
    pd001m1_filename: str
    visit_monitor_enabled: bool
    poll_interval_ms: int
    browser_refresh_ms: int
    new_highlight_seconds: int
    room_count: int
    doctor_room_map: dict[str, int]
    patient_number_field: str
    patient_name_field: str
    sqlite_path: Path
    log_path: Path

    @property
    def visit_filename(self) -> str:
        """Compatibility name for the configurable VISIT source filename."""
        return self.rg011m1_visit_filename


def _resolve_config_path(config_file: Path, value: Any, default: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        value = default
    path = Path(value)
    if not path.is_absolute():
        path = config_file.parent / path
    return path.resolve()


def _is_within(path: Path, directory: Path) -> bool:
    try:
        common = os.path.commonpath((str(path), str(directory)))
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(str(directory))


def _require_int(settings: dict[str, Any], name: str, default: int, *, minimum: int) -> int:
    value = settings.get(name, default)
    if type(value) is not int or value < minimum:
        raise ConfigurationError(f"{name} must be an integer greater than or equal to {minimum}.")
    return value


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load a JSON config and resolve its writable paths beside the config file."""
    if config_path is None:
        config_path = os.environ.get("CLINIC_QUEUE_CONFIG", "config.json")
    path = Path(config_path).expanduser().resolve()
    if not path.is_file():
        raise ConfigurationError(
            f"Configuration file not found: {path}. Copy config.example.json to config.json, "
            "then edit the HIS path and other local settings."
        )

    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Cannot read configuration file {path}: {exc}") from exc
    if not isinstance(settings, dict):
        raise ConfigurationError(f"Configuration file must contain a JSON object: {path}")

    bind_host = settings.get("bind_host", "0.0.0.0")
    if not isinstance(bind_host, str) or not bind_host.strip():
        raise ConfigurationError("bind_host must be a non-empty interface address.")

    port = _require_int(settings, "port", 8000, minimum=1)
    if port > 65535:
        raise ConfigurationError("port must be between 1 and 65535.")

    his_value = settings.get("his_data_path", DEFAULT_HIS_DATA_PATH)
    if not isinstance(his_value, str) or not his_value.strip():
        raise ConfigurationError("his_data_path must be a non-empty directory path.")
    his_data_path = Path(his_value).expanduser().resolve()

    rg011m1_filename = settings.get("rg011m1_filename", "RG011M1.DBF")
    rg011m1_visit_filename = settings.get(
        "rg011m1_visit_filename",
        settings.get("visit_filename", DEFAULT_VISIT_FILENAME),
    )
    pd001m1_filename = settings.get("pd001m1_filename", "PD001M1.DBF")
    for name, value in (
        ("rg011m1_filename", rg011m1_filename),
        ("rg011m1_visit_filename", rg011m1_visit_filename),
        ("pd001m1_filename", pd001m1_filename),
    ):
        if not isinstance(value, str) or not value.strip() or Path(value).name != value:
            raise ConfigurationError(f"{name} must be a filename, not a path.")

    visit_monitor_enabled = settings.get("visit_monitor_enabled", False)
    if type(visit_monitor_enabled) is not bool:
        raise ConfigurationError("visit_monitor_enabled must be a boolean.")

    poll_interval_ms = _require_int(settings, "poll_interval_ms", 500, minimum=100)
    browser_refresh_ms = _require_int(settings, "browser_refresh_ms", 1000, minimum=100)
    new_highlight_seconds = _require_int(settings, "new_highlight_seconds", 300, minimum=0)
    room_count = _require_int(settings, "room_count", 2, minimum=2)
    if room_count != 2:
        raise ConfigurationError("room_count must be 2 for the clinic queue MVP.")

    doctor_room_map = settings.get("doctor_room_map", {})
    if not isinstance(doctor_room_map, dict) or any(
        not isinstance(code, str)
        or not code.strip()
        or type(room) is not int
        or room not in (1, 2)
        for code, room in doctor_room_map.items()
    ):
        raise ConfigurationError(
            "legacy doctor_room_map must map non-empty doctor codes to room 1 or 2."
        )

    patient_number_field = settings.get("patient_number_field", "NUM")
    patient_name_field = settings.get("patient_name_field", "")
    if not isinstance(patient_number_field, str) or not patient_number_field.strip():
        raise ConfigurationError("patient_number_field must be a non-empty field name.")
    if not isinstance(patient_name_field, str):
        raise ConfigurationError("patient_name_field must be a string; use an empty string if unknown.")

    sqlite_path = _resolve_config_path(path, settings.get("sqlite_path"), "data/clinic_queue.sqlite3")
    log_path = _resolve_config_path(path, settings.get("log_path"), "logs/app.log")
    if _is_within(sqlite_path, his_data_path) or _is_within(log_path, his_data_path):
        raise ConfigurationError("SQLite and log files must be outside the HIS data directory.")

    return AppConfig(
        bind_host=bind_host.strip(),
        port=port,
        his_data_path=his_data_path,
        rg011m1_filename=rg011m1_filename,
        rg011m1_visit_filename=rg011m1_visit_filename,
        pd001m1_filename=pd001m1_filename,
        visit_monitor_enabled=visit_monitor_enabled,
        poll_interval_ms=poll_interval_ms,
        browser_refresh_ms=browser_refresh_ms,
        new_highlight_seconds=new_highlight_seconds,
        room_count=room_count,
        doctor_room_map=dict(doctor_room_map),
        patient_number_field=patient_number_field.strip(),
        patient_name_field=patient_name_field.strip(),
        sqlite_path=sqlite_path,
        log_path=log_path,
    )
