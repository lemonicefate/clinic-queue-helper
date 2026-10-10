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
    assert "always-on" in readme
    assert "accepted and ignored" in readme
    assert "experimental" not in readme.lower()
    assert "看診中（實驗）" not in readme
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
    assert "visit_monitor_enabled" not in lower_procedure
    assert "看診中（實驗）" not in procedure
    assert "experimental" not in lower_procedure


def test_visit_card_domain_docs_record_the_superseding_decision():
    glossary = (PROJECT_ROOT / "GLOSSARY.md").read_text(encoding="utf-8")
    context = (PROJECT_ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    historical_adr = (PROJECT_ROOT / "docs" / "adr" / "0005-experimental-visit-current-patient-observation.md").read_text(
        encoding="utf-8"
    )
    accepted_adr = (PROJECT_ROOT / "docs" / "adr" / "0006-default-visit-current-patient-card.md").read_text(
        encoding="utf-8"
    )
    specification = (PROJECT_ROOT / "SPEC.md").read_text(encoding="utf-8")

    assert "看診中卡片" in glossary
    assert "候診卡片暫隱" in glossary
    assert "看診中卡片" in context
    assert "候診卡片暫隱" in context
    assert "Status: superseded by ADR 0006" in historical_adr
    assert "Status: accepted" in accepted_adr
    assert "Default VISIT current-patient card" in accepted_adr
    assert "Follow-up: Default VISIT current-patient card" in specification
