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
