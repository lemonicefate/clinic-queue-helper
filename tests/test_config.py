import json
from pathlib import Path

import pytest

from clinic_queue.config import ConfigurationError, load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]

def test_config_resolves_application_paths_relative_to_config_file(tmp_path):
    his_path = tmp_path / "his"
    his_path.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "bind_host": "0.0.0.0",
                "port": 8123,
                "his_data_path": str(his_path),
                "rg011m1_filename": "RG011M1.DBF",
                "pd001m1_filename": "PD001M1.DBF",
                "poll_interval_ms": 500,
                "browser_refresh_ms": 1000,
                "new_highlight_seconds": 300,
                "room_count": 2,
                "doctor_room_map": {"DOC1": 1},
                "patient_number_field": "NUM",
                "patient_name_field": "NAME",
                "sqlite_path": "state/queue.sqlite3",
                "log_path": "logs/app.log",
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.bind_host == "0.0.0.0"
    assert config.port == 8123
    assert config.sqlite_path == (tmp_path / "state" / "queue.sqlite3").resolve()
    assert config.log_path == (tmp_path / "logs" / "app.log").resolve()


def test_example_config_contains_required_mvp_settings():
    config = load_config(PROJECT_ROOT / "config.example.json")

    assert config.rg011m1_filename == "RG011M1.DBF"
    assert config.pd001m1_filename == "PD001M1.DBF"
    assert config.poll_interval_ms == 500
    assert config.browser_refresh_ms == 1000
    assert config.new_highlight_seconds == 300
    assert config.room_count == 2
    assert config.patient_number_field == "NUM"


def test_configuration_rejects_application_files_inside_his_directory(tmp_path):
    his_path = tmp_path / "his"
    his_path.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "his_data_path": str(his_path),
                "sqlite_path": str(his_path / "queue.sqlite3"),
                "log_path": str(tmp_path / "logs" / "app.log"),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="outside the HIS data directory"):
        load_config(config_path)
