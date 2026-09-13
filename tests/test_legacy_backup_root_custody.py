from pathlib import Path

import pytest

from backup_verify_lam1688 import BackupVerifier


def test_run_rejects_symlinked_backup_root_before_any_backup_or_report_write(tmp_path, monkeypatch):
    target = tmp_path / "external-backup"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_text("do not touch", encoding="utf-8")
    backup_link = tmp_path / "backup"
    try:
        backup_link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    verifier = BackupVerifier(backup_link)
    monkeypatch.setattr(
        verifier,
        "verify_backup_files",
        lambda: pytest.fail("linked backup root must fail before file verification"),
    )
    monkeypatch.setattr(
        verifier,
        "verify_database_integrity",
        lambda: pytest.fail("linked backup root must fail before sqlite verification"),
    )
    monkeypatch.setattr(
        verifier,
        "test_restoration",
        lambda: pytest.fail("linked backup root must fail before restoration"),
    )
    monkeypatch.setattr(
        verifier,
        "save_report",
        lambda report: pytest.fail("linked backup root must fail before report publication"),
    )

    results = verifier.run()

    assert results["integrity"] is False
    assert results["restoration"] is False
    assert results["errors"] == ["Unsafe backup root: expected a real directory"]
    assert sentinel.read_text(encoding="utf-8") == "do not touch"
    assert not (target / "verification_report.txt").exists()


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_backup_root_fails_closed(kind, tmp_path):
    backup = tmp_path / "backup"
    if kind == "file":
        backup.write_text("not a directory", encoding="utf-8")

    verifier = BackupVerifier(backup)

    assert verifier.validate_backup_root() is False
    if kind == "missing":
        assert verifier.results["errors"] == ["Invalid backup root: path does not exist"]
    else:
        assert verifier.results["errors"] == ["Unsafe backup root: expected a real directory"]


def test_real_backup_directory_remains_valid(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()

    verifier = BackupVerifier(backup)

    assert verifier.validate_backup_root() is True
    assert verifier.results["errors"] == []
