from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_windows_setup_configuration_and_lan_url():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "pip install -r requirements.txt" in readme
    assert "Copy-Item config.example.json config.json" in readme
    assert ".\\run.ps1" in readme
    assert "http://<server-lan-ip>:8000" in readme
    assert "patient_name_field" in readme
    assert "/?room=1" in readme
    assert "Browsers check for shared queue changes every second" in readme
    assert "never write back to HIS files" in readme
