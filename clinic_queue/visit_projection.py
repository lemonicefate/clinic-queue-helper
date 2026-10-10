"""Project the read-only VISIT card onto the visible queue board."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _stable_candidate(room_monitor: dict[str, Any]) -> dict[str, Any] | None:
    """Return the one safe candidate that may hide matching waiting cards."""
    if room_monitor.get("status") not in {"UNKNOWN", "STALE"}:
        return None
    candidates = room_monitor.get("candidates", [])
    if len(candidates) != 1 or candidates[0].get("status") != "UNKNOWN":
        return None
    return candidates[0]


def project_visit_monitor(
    board: dict[str, Any],
    visit_monitor: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a filtered board and its matching monitor projection.

    The QueueStore board is copied before presentation fields are added.  The
    persisted ``queue_position`` remains untouched; ``display_position`` is a
    derived number for the visible waiting cards after temporary suppression.
    Only recognized room waiting lists participate in suppression, so away,
    preregistered, completed, unmatched, and unconfirmed groups remain intact.
    """
    projected_board = deepcopy(board)
    projected_monitor = deepcopy(visit_monitor)
    suppressed: list[str] = []

    for room_id, room_monitor in projected_monitor.get("rooms", {}).items():
        candidate = _stable_candidate(room_monitor)
        if candidate is None:
            continue
        room_key = int(room_id)
        room_board = projected_board.get("rooms", {}).get(room_key)
        if room_board is None:
            room_board = projected_board.get("rooms", {}).get(str(room_id))
        if room_board is None:
            continue

        candidate_num = str(candidate.get("patient_no", "")).strip()
        candidate_date = str(
            candidate.get("visit_date", projected_board.get("clinic_date", ""))
        ).strip()
        candidate_time_kind = candidate.get("time_kind")
        visible_waiting: list[dict[str, Any]] = []
        for encounter in room_board.get("waiting", []):
            same_scope = (
                str(encounter.get("patient_no", "")).strip() == candidate_num
                and str(encounter.get("visit_date", "")).strip() == candidate_date
                and encounter.get("room_id") == room_key
                and encounter.get("time_kind") == candidate_time_kind
            )
            if same_scope:
                suppressed.append(str(encounter["encounter_key"]))
            else:
                visible_waiting.append(encounter)
        room_board["waiting"] = visible_waiting

    suppressed = sorted(set(suppressed))
    projected_monitor["suppressed_encounter_keys"] = suppressed

    for room_board in projected_board.get("rooms", {}).values():
        for display_position, encounter in enumerate(room_board.get("waiting", []), start=1):
            encounter["display_position"] = display_position

    return projected_board, projected_monitor

