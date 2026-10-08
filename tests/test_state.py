import logging

import pytest

from clinic_queue.state import HISState, PresenceState, resolve_his_state


@pytest.mark.parametrize(
    ("raw_fields", "expected_state", "expected_presence", "expected_active"),
    [
        ({"_DELETED": True, "OVER": "T", "TREAT": "Y"}, HISState.INVALID, None, False),
        ({"OVER": "", "TREAT": "N"}, HISState.WAITING, PresenceState.PRESENT, True),
        ({"OVER": "F", "TREAT": "N"}, HISState.WAITING, PresenceState.PRESENT, True),
        ({"OVER": "T", "TREAT": "N"}, HISState.COMPLETED, None, False),
        ({"OVER": "", "TREAT": "B"}, HISState.WAITING, PresenceState.PRESENT, True),
        ({"OVER": "", "TREAT": "Y"}, HISState.COMPLETED, None, False),
        ({"OVER": "", "TREAT": "C"}, HISState.PREREGISTERED, None, True),
        ({"OVER": "F", "TREAT": "C"}, HISState.PREREGISTERED, None, True),
        ({"OVER": "T", "TREAT": "C"}, HISState.COMPLETED, None, False),
        ({"OVER": "F", "TREAT": "Y"}, HISState.COMPLETED, None, False),
    ],
)
def test_resolves_known_his_states(raw_fields, expected_state, expected_presence, expected_active):
    resolved = resolve_his_state(raw_fields)

    assert resolved.state is expected_state
    assert resolved.initial_presence is expected_presence
    assert resolved.active is expected_active


def test_unknown_his_state_defaults_to_visible_waiting_present_and_logs_raw_values(caplog):
    with caplog.at_level(logging.WARNING, logger="clinic_queue.state"):
        resolved = resolve_his_state({"OVER": "?", "TREAT": "Z"})

    assert resolved.state is HISState.WAITING
    assert resolved.initial_presence is PresenceState.PRESENT
    assert resolved.active is True
    assert "OVER='?'" in caplog.text
    assert "TREAT='Z'" in caplog.text


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("1", 1), (" 2 ", 2), ("3", 3), ("", None), (None, None), ("0", None), ("3.0", None)],
)
def test_normalizes_only_supported_time_kind_values(raw_value, expected):
    from clinic_queue.state import normalize_time_kind

    assert normalize_time_kind(raw_value) == expected
