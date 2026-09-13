import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_verify_lam1688 as legacy


def _write_other_required_files(backup: Path) -> None:
    (backup / "wallets.db").write_bytes(b"wallets")
    (backup / "miner_state.json").write_text("{}")


def test_required_symlink_is_rejected_before_checksum(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    _write_other_required_files(backup)
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"external database")
    (backup / "rustchain.db").symlink_to(outside)

    verifier = legacy.BackupVerifier(backup)

    assert verifier.verify_backup_files() is False
    assert "rustchain.db" not in verifier.results["checksums"]
    assert any("Unsafe file type or link: rustchain.db" == error for error in verifier.results["errors"])


def test_database_integrity_rejects_symlink_before_sqlite(tmp_path, monkeypatch):
    backup = tmp_path / "backup"
    backup.mkdir()
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"not sqlite")
    (backup / "rustchain.db").symlink_to(outside)

    monkeypatch.setattr(
        legacy.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("sqlite3 must not receive a linked database"),
    )

    verifier = legacy.BackupVerifier(backup)
    assert verifier.verify_database_integrity() is False
    assert verifier.results["integrity"] is False
    assert any("Unsafe database file type or link" in error for error in verifier.results["errors"])


def test_required_hardlink_is_rejected(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    _write_other_required_files(backup)
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"external database")
    try:
        os.link(outside, backup / "rustchain.db")
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")

    verifier = legacy.BackupVerifier(backup)
    assert verifier.verify_backup_files() is False
    assert "rustchain.db" not in verifier.results["checksums"]
    assert any("Unsafe file type or link: rustchain.db" == error for error in verifier.results["errors"])


def test_restoration_rejects_linked_optional_entry(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    for name, payload in (
        ("rustchain.db", b"db"),
        ("wallets.db", b"wallet"),
        ("miner_state.json", b"{}"),
    ):
        (backup / name).write_bytes(payload)
    outside = tmp_path / "outside.txt"
    outside.write_text("do not copy")
    (backup / "extra.txt").symlink_to(outside)

    verifier = legacy.BackupVerifier(backup)
    assert verifier.test_restoration() is False
    assert any("Unsafe backup entry: extra.txt" in error for error in verifier.results["errors"])
    assert outside.read_text() == "do not copy"


def test_report_publication_replaces_symlink_without_touching_target(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("sentinel")
    report_path = backup / "verification_report.txt"
    report_path.symlink_to(outside)

    verifier = legacy.BackupVerifier(backup)
    written = verifier.save_report("safe report\n")

    assert written == report_path
    assert not report_path.is_symlink()
    assert report_path.read_text() == "safe report\n"
    assert outside.read_text() == "sentinel"
