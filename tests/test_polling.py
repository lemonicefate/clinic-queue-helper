import hashlib
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from clinic_queue.queue import QueueStore
from clinic_queue.state import PresenceState
from tests.dbf_fixtures import write_dbf
from tests.test_queue import create_config


FIELDS = [
    ("NUM", "C", 6),
    ("TETDAY", "D", 8),
    ("CCDATE", "D", 8),
    ("CCTIME", "C", 4),
    ("CCDOC", "C", 6),
    ("OVER", "C", 1),
    ("TREAT", "C", 1),
    ("TIME_KIND", "C", 1),
    ("GINO1", "C", 10),
    ("RELKEY", "C", 12),
]


def rg_row(
    number,
    *,
    doctor="DOC1",
    treat="N",
    over="",
    time_kind="1",
    deleted=False,
    visit_day=None,
):
    return {
        "_DELETED": deleted,
        "NUM": number,
        "TETDAY": (visit_day or date.today()).strftime("%Y%m%d"),
        "CCDATE": date.today().strftime("%Y%m%d"),
        "CCTIME": "0900",
        "CCDOC": doctor,
        "OVER": over,
        "TREAT": treat,
        "TIME_KIND": time_kind,
        "GINO1": f"151007{int(number[-4:]):04d}",
        "RELKEY": f"REL-{number}",
    }


def make_source(config, rows):
    return write_dbf(config.his_data_path / config.rg011m1_filename, FIELDS, rows)


def make_poller(config, *, recovery_interval_seconds=45):
    from clinic_queue.polling import HISPoller

    store = QueueStore(config.sqlite_path)
    store.initialize_room_doctor_codes(config.doctor_room_map)
    return HISPoller(config, store, recovery_interval_seconds=recovery_interval_seconds), store


def test_incremental_poller_detects_appends_and_does_not_change_his_hash(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    source = make_source(config, [rg_row("100001")])
    poller, store = make_poller(config)
    poller.initialize()

    make_source(config, [rg_row("100001"), rg_row("100002")])
    source_hash_before = hashlib.sha256(source.read_bytes()).hexdigest()
    poller.poll_once()
    source_hash_after = hashlib.sha256(source.read_bytes()).hexdigest()

    assert [entry["patient_no"] for entry in store.get_board()["rooms"][1]["waiting"]] == [
        "100001",
        "100002",
    ]
    assert source_hash_after == source_hash_before


def test_tracked_record_changes_reconcile_completion_and_deletion(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    source = make_source(config, [rg_row("100001")])
    poller, store = make_poller(config)
    poller.initialize()

    make_source(config, [rg_row("100001", over="F")])
    poller.poll_once()
    assert store.get_board(1)["rooms"][1]["waiting"][0]["queue_status"] == "WAITING"

    make_source(config, [rg_row("100001", treat="Y")])
    poller.poll_once()
    assert [entry["encounter_key"] for entry in store.get_board()["completed"]] == ["relkey:REL-100001"]

    make_source(config, [rg_row("100001", deleted=True)])
    poller.poll_once()
    board = store.get_board()
    assert [entry["encounter_key"] for entry in board["invalidated"]] == ["relkey:REL-100001"]
    assert board["rooms"][1]["waiting"] == []
    assert source.exists()


def test_poller_retries_a_partially_written_appended_record(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    source = make_source(config, [rg_row("100001")])
    poller, store = make_poller(config)
    poller.initialize()

    make_source(config, [rg_row("100001"), rg_row("100002")])
    header_size = 32 + len(FIELDS) * 32 + 1
    record_size = 1 + sum(width for _, _, width in FIELDS)
    source.write_bytes(source.read_bytes()[: header_size + record_size + 2])
    poller.poll_once()

    assert [entry["patient_no"] for entry in store.get_board()["rooms"][1]["waiting"]] == ["100001"]

    make_source(config, [rg_row("100001"), rg_row("100002")])
    poller.poll_once()

    assert [entry["patient_no"] for entry in store.get_board()["rooms"][1]["waiting"]] == [
        "100001",
        "100002",
    ]


def test_recovery_scan_discovers_a_same_count_record_added_outside_the_append_range(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    make_source(config, [rg_row("100001"), rg_row("100002", visit_day=date.today() - timedelta(days=1))])
    poller, store = make_poller(config, recovery_interval_seconds=0)
    poller.initialize()

    make_source(config, [rg_row("100001"), rg_row("100002")])
    poller.poll_once()

    assert [entry["patient_no"] for entry in store.get_board()["rooms"][1]["waiting"]] == [
        "100001",
        "100002",
    ]


def test_restart_reconciliation_preserves_staff_state_and_prunes_other_days(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1, "DOC2": 2})
    make_source(
        config,
        [rg_row("100001"), rg_row("100002", doctor="DOC2"), rg_row("100003", doctor="DOC2")],
    )
    poller, store = make_poller(config)
    poller.initialize()
    store.set_presence("relkey:REL-100001", PresenceState.AWAY)
    store.set_overdue("relkey:REL-100002", True)
    store.reorder("relkey:REL-100003", 1)
    stale = replace(
        poller.tracked_by_recno[1],
        encounter_key="relkey:OLD-DAY",
        recno=99,
        visit_date=date.today() - timedelta(days=1),
    )
    store.reconcile([stale], config.doctor_room_map)
    with sqlite3.connect(config.sqlite_path) as connection:
        connection.execute(
            "UPDATE encounter_state SET queue_status = 'IN_CONSULTATION', current_flag = 1 "
            "WHERE encounter_key = ?",
            ("relkey:REL-100002",),
        )

    restarted, restarted_store = make_poller(config)
    restarted.initialize()

    board = restarted_store.get_board()
    away = board["rooms"][1]["away"][0]
    room_two_entries = board["rooms"][2]["waiting"]
    room_two = next(entry for entry in room_two_entries if entry["encounter_key"] == "relkey:REL-100002")
    assert away["encounter_key"] == "relkey:REL-100001"
    assert away["presence_override"] is True
    assert room_two["encounter_key"] == "relkey:REL-100002"
    assert room_two["room_id"] == 2
    assert "room_override" not in room_two
    assert room_two["overdue"] is True
    assert "current_flag" not in room_two
    assert room_two["queue_status"] == "WAITING"
    assert [entry["encounter_key"] for entry in room_two_entries] == [
        "relkey:REL-100003",
        "relkey:REL-100002",
    ]
    with sqlite3.connect(config.sqlite_path) as connection:
        visit_dates = [row[0] for row in connection.execute("SELECT visit_date FROM encounter_state")]
    assert set(visit_dates) == {date.today().isoformat()}


def test_new_his_encounter_appends_to_its_room_and_session_order(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    make_source(
        config,
        [rg_row("100001", time_kind="1"), rg_row("100002", time_kind="2")],
    )
    poller, store = make_poller(config)
    poller.initialize()

    make_source(
        config,
        [
            rg_row("100001", time_kind="1"),
            rg_row("100002", time_kind="2"),
            rg_row("100003", time_kind="1"),
        ],
    )
    poller.poll_once()

    morning = store.get_board(1)["rooms"][1]["waiting"]
    noon = store.get_board(2)["rooms"][1]["waiting"]
    assert [entry["patient_no"] for entry in morning] == ["100001", "100003"]
    assert [entry["queue_position"] for entry in morning] == [1, 2]
    assert [entry["patient_no"] for entry in noon] == ["100002"]
    assert [entry["queue_position"] for entry in noon] == [1]


def test_doctor_change_invalidates_old_row_and_highlights_new_room_tail(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1, "DOC2": 2})
    make_source(config, [rg_row("100001"), rg_row("100002")])
    poller, store = make_poller(config)
    poller.initialize()

    make_source(
        config,
        [
            rg_row("100001", deleted=True),
            rg_row("100002"),
            rg_row("100003", doctor="DOC2"),
        ],
    )
    poller.poll_once()

    board = store.get_board()
    assert [entry["encounter_key"] for entry in board["invalidated"]] == ["relkey:REL-100001"]
    assert [entry["encounter_key"] for entry in board["rooms"][2]["waiting"]] == ["relkey:REL-100003"]
    assert board["rooms"][2]["waiting"][0]["is_new"] is True


def test_incremental_his_change_uses_ccdoc_filter_and_preserves_presence_and_overdue(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1, "DOC2": 2})
    make_source(config, [rg_row("100001")])
    poller, store = make_poller(config)
    poller.initialize()
    store.set_presence("relkey:REL-100001", PresenceState.AWAY)
    store.set_overdue("relkey:REL-100001", True)

    make_source(config, [rg_row("100001", doctor="DOC2", treat="C")])
    poller.poll_once()

    entry = store.get_board()["rooms"][2]["preregistered"][0]
    assert entry["doctor_code"] == "DOC2"
    assert entry["room_id"] == 2
    assert "room_override" not in entry
    assert entry["presence_status"] == "AWAY"
    assert entry["presence_override"] is True
    assert entry["overdue"] is True


def test_saved_room_filter_wins_over_stale_polling_configuration(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1, "DOC2": 2})
    make_source(config, [rg_row("100001", doctor="DOC1")])
    poller, store = make_poller(config)
    poller.initialize()
    store.set_room_doctor_code(1, "DOCX")

    poller.poll_once()

    board = store.get_board()
    assert board["room_doctor_codes"] == {1: "DOCX", 2: "DOC2"}
    assert board["rooms"][1]["waiting"] == []
    assert [entry["encounter_key"] for entry in board["unmatched"]["waiting"]] == [
        "relkey:REL-100001",
    ]


def test_new_highlight_expires_and_manual_reorder_clears_it(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    make_source(config, [rg_row("100001"), rg_row("100002")])
    poller, store = make_poller(config)
    poller.initialize()

    board = store.get_board()
    assert all(entry["is_new"] for entry in board["rooms"][1]["waiting"])

    with sqlite3.connect(config.sqlite_path) as connection:
        connection.execute(
            "UPDATE encounter_state SET new_highlight_until = ? WHERE encounter_key = ?",
            (
                (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                "relkey:REL-100001",
            ),
        )
    store.reorder("relkey:REL-100002", 1)
    board = store.get_board()
    highlights = {entry["encounter_key"]: entry["is_new"] for entry in board["rooms"][1]["waiting"]}
    assert highlights["relkey:REL-100001"] is False
    assert highlights["relkey:REL-100002"] is False


def test_his_outage_keeps_last_queue_and_successful_retry_clears_banner(tmp_path):
    config = create_config(tmp_path, {"DOC1": 1})
    source = make_source(
        config,
        [rg_row("100001"), rg_row("100002", visit_day=date.today() - timedelta(days=1))],
    )
    poller, store = make_poller(config)
    poller.initialize()
    source.unlink()

    poller.poll_once()

    assert poller.his_stale is True
    assert [entry["patient_no"] for entry in store.get_board()["rooms"][1]["waiting"]] == ["100001"]

    make_source(config, [rg_row("100001"), rg_row("100002")])
    poller.poll_once()

    assert poller.his_stale is False
    assert [entry["patient_no"] for entry in store.get_board()["rooms"][1]["waiting"]] == [
        "100001",
        "100002",
    ]


def test_sqlite_failure_is_reported_without_marking_his_stale_or_writing_his(tmp_path, monkeypatch):
    config = create_config(tmp_path, {"DOC1": 1})
    source = make_source(config, [rg_row("100001")])
    poller, store = make_poller(config)
    poller.initialize()
    make_source(config, [rg_row("100001", treat="B")])
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    def fail_reconcile(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "reconcile", fail_reconcile)
    poller.poll_once()

    assert poller.his_stale is False
    assert "database is locked" in poller.storage_error
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
