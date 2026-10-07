import asyncio
import json
from datetime import date

import httpx

from clinic_queue.app import create_app
from clinic_queue.config import load_config
from tests.dbf_fixtures import write_dbf


def test_browser_home_page_opens_with_queue_heading(tmp_path):
    his_path = tmp_path / "his"
    his_path.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "his_data_path": str(his_path),
                "sqlite_path": "state/queue.sqlite3",
                "log_path": "logs/app.log",
            }
        ),
        encoding="utf-8",
    )
    app = create_app(load_config(config_path))

    async def fetch_home_page():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/")

    response = asyncio.run(fetch_home_page())

    assert response.status_code == 200
    assert "Clinic Queue Helper" in response.text
    assert "候診隊列" in response.text
    assert "HIS 資料暫時無法同步" in response.text


def test_home_page_shows_today_patient_name_and_queue_number_from_dbfs(tmp_path):
    his_path = tmp_path / "his"
    his_path.mkdir()
    today = date.today()
    today_raw = today.strftime("%Y%m%d")
    write_dbf(
        his_path / "RG011M1.DBF",
        [
            ("NUM", "C", 6),
            ("TETDAY", "D", 8),
            ("CCDATE", "D", 8),
            ("CCTIME", "C", 4),
            ("CCDOC", "C", 6),
            ("OVER", "C", 1),
            ("TREAT", "C", 1),
            ("GINO1", "C", 10),
            ("RELKEY", "C", 12),
        ],
        [
            {
                "NUM": "100001",
                "TETDAY": today_raw,
                "CCDATE": "20261001",
                "CCTIME": "0900",
                "CCDOC": "DOC1",
                "OVER": "",
                "TREAT": "N",
                "GINO1": "1510070053",
                "RELKEY": "REL-001",
            }
        ],
    )
    write_dbf(
        his_path / "PD001M1.DBF",
        [("CHARTNO", "C", 6), ("FULLNAME", "C", 20)],
        [{"CHARTNO": "100001", "FULLNAME": "王小明"}],
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "his_data_path": str(his_path),
                "patient_number_field": "CHARTNO",
                "patient_name_field": "FULLNAME",
                "sqlite_path": "state/queue.sqlite3",
                "log_path": "logs/app.log",
            }
        ),
        encoding="utf-8",
    )

    app = create_app(load_config(config_path))

    async def fetch_home_page():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/")

    response = asyncio.run(fetch_home_page())

    assert response.status_code == 200
    assert "王小明" in response.text
    assert "#53" in response.text
    assert "100001" in response.text
    assert "HIS 資料暫時無法同步" not in response.text
