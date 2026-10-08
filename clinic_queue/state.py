"""Resolve raw Visual FoxPro state fields into queue-facing state."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


logger = logging.getLogger("clinic_queue.state")


class HISState(str, Enum):
    INVALID = "INVALID"
    COMPLETED = "COMPLETED"
    PREREGISTERED = "PREREGISTERED"
    WAITING = "WAITING"


class PresenceState(str, Enum):
    PRESENT = "PRESENT"
    AWAY = "AWAY"


TIME_KIND_LABELS = {1: "早診", 2: "午診", 3: "晚診"}


def normalize_time_kind(value: Any) -> int | None:
    """Return a supported clinic-session identity, leaving uncertain values unknown."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    normalized = str(value).strip()
    return int(normalized) if normalized in {"1", "2", "3"} else None


@dataclass(frozen=True)
class ResolvedHISState:
    state: HISState
    initial_presence: PresenceState | None
    active: bool


def _field(fields: Mapping[str, Any], name: str, default: Any = "") -> Any:
    for key, value in fields.items():
        if key.upper() == name.upper():
            return value
    return default


def _value(fields: Mapping[str, Any], name: str) -> str:
    value = _field(fields, name)
    if value is None:
        return ""
    return str(value).strip().upper()


def _is_deleted(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    return str(value).strip().upper() in {"T", "TRUE", "Y", "1", "*"}


def resolve_his_state(fields: Mapping[str, Any]) -> ResolvedHISState:
    """Apply the spec precedence while preserving unknown records as visible."""
    deleted = _is_deleted(_field(fields, "_DELETED", False))
    over = _value(fields, "OVER")
    treat = _value(fields, "TREAT")

    if deleted:
        return ResolvedHISState(HISState.INVALID, None, False)
    if over == "T" or treat == "Y":
        return ResolvedHISState(HISState.COMPLETED, None, False)
    if treat == "C":
        return ResolvedHISState(HISState.PREREGISTERED, PresenceState.AWAY, True)
    if over == "F" or treat in {"B", "N"}:
        return ResolvedHISState(HISState.WAITING, PresenceState.PRESENT, True)

    logger.warning("Unknown HIS state combination: OVER=%r, TREAT=%r", over, treat)
    return ResolvedHISState(HISState.WAITING, PresenceState.PRESENT, True)
