from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_windows_setup_configuration_and_lan_url():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "pip install -r requirements.txt" in readme
    assert "Copy-Item config.example.json config.json" in readme
    assert ".\\run.ps1" in readme
    assert "http://<server-lan-ip>:8000" in readme
    assert "patient_name_field" in readme
    assert "rg011m1_visit_filename" in readme
    assert "RG011M1_VISIT.DBF" in readme
    assert "visit_monitor_enabled" in readme
    assert "defaults to `false`" in readme
    assert "experimental, read-only" in readme
    assert "docs/visit-manual-validation.md" in readme
    assert "/?room=1" in readme
    assert "Browsers check for shared queue changes every second" in readme
    assert "never write back to HIS files" in readme


def test_visit_manual_validation_procedure_is_controlled_and_keeps_captures_outside_repo():
    procedure = (PROJECT_ROOT / "docs" / "visit-manual-validation.md").read_text(
        encoding="utf-8"
    )

    for required_heading in (
        "Baseline capture",
        "Call and completion/deletion",
        "Repeated calls and rapid switching",
        "Reopening a called patient",
        "Completed-detail-page noise",
        "Another physician or session",
        "Assumptions and unresolved behavior",
    ):
        assert required_heading in procedure

    lower_procedure = " ".join(procedure.lower().split())
    assert (
        "all raw captures must stay in a clinic-approved, access-controlled location "
        "outside this repository and outside git, github, ci, the issue tracker, issue comments, "
        "and pull requests"
    ) in lower_procedure
    assert (
        "never copy patient numbers, names, raw dbf bytes, screenshots, or logs into it"
    ) in lower_procedure
    assert "do not use real patient data or real phi" in lower_procedure
    assert "active" in lower_procedure
