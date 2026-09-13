from types import SimpleNamespace

import backup_verify_lam1688
from backup_verify_lam1688 import BackupVerifier


def test_required_directory_path_fails_verification_without_crashing(tmp_path):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()

    # Exists, but cannot be opened as a file for SHA-256 calculation.
    (backup_dir / "rustchain.db").mkdir()
    (backup_dir / "wallets.db").write_bytes(b"wallet-bytes")
    (backup_dir / "miner_state.json").write_text('{"height": 42}')

    verifier = BackupVerifier(backup_dir)

    assert verifier.verify_backup_files() is False
    assert verifier.results["checksums"]["rustchain.db"] is None
    assert any(
        error.startswith("Error calculating checksum:")
        for error in verifier.results["errors"]
    )


def test_valid_required_files_still_verify(tmp_path):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "rustchain.db").write_bytes(b"database-bytes")
    (backup_dir / "wallets.db").write_bytes(b"wallet-bytes")
    (backup_dir / "miner_state.json").write_text('{"height": 42}')

    verifier = BackupVerifier(backup_dir)

    assert verifier.verify_backup_files() is True
    assert all(verifier.results["checksums"].values())
    assert verifier.results["errors"] == []


def _integrity_verifier(tmp_path):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "rustchain.db").write_bytes(b"database-bytes")
    return BackupVerifier(backup_dir)


def test_database_integrity_accepts_exact_ok_on_zero_exit(tmp_path, monkeypatch):
    verifier = _integrity_verifier(tmp_path)
    monkeypatch.setattr(
        backup_verify_lam1688.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="  OK\n",
            stderr="",
        ),
    )

    assert verifier.verify_database_integrity() is True
    assert verifier.results["integrity"] is True
    assert verifier.results["errors"] == []


def test_database_integrity_rejects_nonzero_exit_even_with_ok_stdout(tmp_path, monkeypatch):
    verifier = _integrity_verifier(tmp_path)
    monkeypatch.setattr(
        backup_verify_lam1688.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="ok\n",
            stderr="sqlite3 failed",
        ),
    )

    assert verifier.verify_database_integrity() is False
    assert verifier.results["integrity"] is False
    assert verifier.results["errors"] == ["Database integrity failed: sqlite3 failed"]


def test_database_integrity_rejects_non_exact_ok_stdout(tmp_path, monkeypatch):
    verifier = _integrity_verifier(tmp_path)
    monkeypatch.setattr(
        backup_verify_lam1688.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="not ok\n",
            stderr="",
        ),
    )

    assert verifier.verify_database_integrity() is False
    assert verifier.results["integrity"] is False
    assert verifier.results["errors"] == ["Database integrity failed: not ok"]


def test_database_integrity_fails_closed_without_sqlite3(tmp_path, monkeypatch):
    verifier = _integrity_verifier(tmp_path)

    def missing_sqlite3(*args, **kwargs):
        raise FileNotFoundError("sqlite3")

    monkeypatch.setattr(backup_verify_lam1688.subprocess, "run", missing_sqlite3)

    assert verifier.verify_database_integrity() is False
    assert verifier.results["integrity"] is False
    assert verifier.results["errors"] == [
        "sqlite3 not available; cannot verify database integrity"
    ]
