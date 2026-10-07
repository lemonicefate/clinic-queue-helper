import os
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_missing_config_command_explains_how_to_create_config(tmp_path):
    environment = os.environ.copy()
    environment["CLINIC_QUEUE_CONFIG"] = str(tmp_path / "missing-config.json")

    result = subprocess.run(
        [sys.executable, "-m", "clinic_queue"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )

    assert result.returncode != 0
    assert "config.example.json" in result.stderr
    assert "doctor-to-room mapping" in result.stderr


def test_windows_launcher_reports_actionable_missing_config(tmp_path):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell is not None
    environment = os.environ.copy()
    environment["CLINIC_QUEUE_CONFIG"] = str(tmp_path / "missing-config.json")

    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PROJECT_ROOT / "run.ps1")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "config.example.json" in output
    assert "config.json" in output


def test_port_conflict_reports_requested_port_and_recovery_action(tmp_path):
    his_path = tmp_path / "his"
    his_path.mkdir()
    config_path = tmp_path / "config.json"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        config_path.write_text(
            json.dumps(
                {
                    "bind_host": "127.0.0.1",
                    "port": port,
                    "his_data_path": str(his_path),
                    "sqlite_path": "state/queue.sqlite3",
                    "log_path": "logs/app.log",
                }
            ),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["CLINIC_QUEUE_CONFIG"] = str(config_path)

        result = subprocess.run(
            [sys.executable, "-m", "clinic_queue"],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert str(port) in output
    assert "already in use" in output


def test_running_service_serves_page_and_creates_app_files_outside_his(tmp_path):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell is not None
    his_path = tmp_path / "his"
    his_path.mkdir()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "bind_host": "127.0.0.1",
                "port": port,
                "his_data_path": str(his_path),
                "sqlite_path": "state/queue.sqlite3",
                "log_path": "logs/app.log",
            }
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["CLINIC_QUEUE_CONFIG"] = str(config_path)
    process = subprocess.Popen(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PROJECT_ROOT / "run.ps1")],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        response_text = None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(f"Service exited before becoming available.\n{stdout}\n{stderr}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.3) as response:
                    response_text = response.read().decode("utf-8")
                break
            except Exception:
                time.sleep(0.1)

        assert response_text is not None
        assert "Clinic Queue Helper" in response_text
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/?room=2", timeout=1) as response:
            room_two_text = response.read().decode("utf-8")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/queue", timeout=1) as response:
            queue_response = json.loads(response.read().decode("utf-8"))
        assert 'aria-label="Room 2"' in room_two_text
        assert 'aria-label="Room 1"' not in room_two_text
        assert queue_response["his_stale"] is True
        assert (tmp_path / "state" / "queue.sqlite3").is_file()
        assert (tmp_path / "logs" / "app.log").is_file()
        assert list(his_path.iterdir()) == []
    finally:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=5,
        )
        process.wait(timeout=5)
