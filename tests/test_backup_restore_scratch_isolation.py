from pathlib import Path

from backup_verify_lam1688 import BackupVerifier


def test_restoration_preserves_preexisting_test_restore_directory(tmp_path):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "rustchain.db").write_bytes(b"database-bytes")
    (backup_dir / "wallets.db").write_bytes(b"wallet-bytes")
    (backup_dir / "miner_state.json").write_text('{"height": 42}')

    preexisting = tmp_path / "test_restore"
    preexisting.mkdir()
    sentinel = preexisting / "do-not-delete.txt"
    sentinel.write_text("owned by someone else")

    verifier = BackupVerifier(backup_dir)

    assert verifier.test_restoration() is True
    assert verifier.results["errors"] == []
    assert sentinel.read_text() == "owned by someone else"
    assert not list(tmp_path.glob("rustchain_restore_*"))


def test_restoration_cleans_its_unique_scratch_directory(tmp_path):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "rustchain.db").write_bytes(b"database-bytes")

    verifier = BackupVerifier(backup_dir)

    assert verifier.test_restoration() is True
    remaining = {path.name for path in tmp_path.iterdir()}
    assert remaining == {"backup"}
