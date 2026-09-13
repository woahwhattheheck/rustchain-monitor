from backup_verify_lam1688 import BackupVerifier


def test_report_keeps_successful_restoration_independent_of_other_errors(tmp_path):
    verifier = BackupVerifier(tmp_path)
    verifier.results["integrity"] = False
    verifier.results["restoration"] = True
    verifier.results["errors"] = ["Database integrity failed: synthetic failure"]

    report = verifier.generate_report()

    assert "## Integrity Status\n✗ FAILED" in report
    assert "## Restoration Test\n✓ PASSED" in report


def test_report_marks_restoration_failure_without_borrowing_integrity_status(tmp_path):
    verifier = BackupVerifier(tmp_path)
    verifier.results["integrity"] = True
    verifier.results["restoration"] = False
    verifier.results["errors"] = ["Restoration test failed: synthetic failure"]

    report = verifier.generate_report()

    assert "## Integrity Status\n✓ PASSED" in report
    assert "## Restoration Test\n✗ FAILED" in report


def test_successful_restoration_records_its_own_status(tmp_path):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "sample.txt").write_text("sample", encoding="utf-8")
    verifier = BackupVerifier(backup_dir)

    assert verifier.test_restoration() is True
    assert verifier.results["restoration"] is True
