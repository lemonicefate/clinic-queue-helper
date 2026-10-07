from clinic_queue.identity import parse_queue_number, resolve_encounter_key


def test_encounter_identity_prefers_non_empty_relkey():
    key = resolve_encounter_key(
        {"RELKEY": "  REL-001  ", "SYS_2015": "SYS-001", "TETDAY": "20261008"},
        recno=7,
    )

    assert key == "relkey:REL-001"


def test_encounter_identity_uses_sys_2015_compound_fallback():
    key = resolve_encounter_key(
        {"SYS_2015": "SYS-001", "TETDAY": "20261008", "NUM": "100001", "GINO1": "1510070053"},
        recno=7,
    )

    assert key == "sys2015:SYS-001|tetday:20261008|num:100001|gino1:1510070053"


def test_encounter_identity_has_a_deterministic_compound_final_fallback():
    fields = {"TETDAY": "20261008", "NUM": "100001", "CCDATE": "20261001", "CCTIME": "0900"}

    first = resolve_encounter_key(fields, recno=7)
    second = resolve_encounter_key(dict(fields), recno=7)

    assert first == second
    assert "recno:7" in first
    assert "tetday:20261008" in first
    assert "num:100001" in first
    assert "ccdate:20261001" in first
    assert "cctime:0900" in first


def test_queue_number_uses_last_four_digits_or_chart_number_fallback():
    assert parse_queue_number("1510070053", "100001") == "53"
    assert parse_queue_number("queue-xx", "100001") == "100001"
