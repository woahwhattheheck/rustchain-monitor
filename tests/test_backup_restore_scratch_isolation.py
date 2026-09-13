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


def test_restoration_rejects_truncated_scratch_copy(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    source = backup_dir / "rustchain.db"
    source.write_bytes(b"database-bytes")

    original_read_bytes = Path.read_bytes

    def truncated_read(path):
        data = original_read_bytes(path)
        if path == source:
            return data[:-1]
        return data

    monkeypatch.setattr(Path, "read_bytes", truncated_read)
    verifier = BackupVerifier(backup_dir)

    assert verifier.test_restoration() is False
    assert verifier.results["restoration"] is False
    assert verifier.results["errors"] == [
        "Restoration test failed: Restoration size mismatch: rustchain.db"
    ]
    assert not list(tmp_path.glob("rustchain_restore_*"))


def test_restoration_rejects_missing_scratch_copy(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "rustchain.db").write_bytes(b"database-bytes")

    original_write_bytes = Path.write_bytes

    def dropped_write(path, data):
        if path.parent.name.startswith("rustchain_restore_"):
            return len(data)
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", dropped_write)
    verifier = BackupVerifier(backup_dir)

    assert verifier.test_restoration() is False
    assert verifier.results["restoration"] is False
    assert verifier.results["errors"] == [
        "Restoration test failed: Unsafe or missing restored file: rustchain.db"
    ]
    assert not list(tmp_path.glob("rustchain_restore_*"))


def test_restoration_rejects_same_size_content_corruption(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "rustchain.db").write_bytes(b"database-bytes")

    verifier = BackupVerifier(backup_dir)
    original_write_bytes = Path.write_bytes

    def corrupt_restored_copy(path, data):
        if path.parent.name.startswith("rustchain_restore_") and data:
            data = bytes([data[0] ^ 1]) + data[1:]
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", corrupt_restored_copy)

    assert verifier.test_restoration() is False
    assert verifier.results["restoration"] is False
    assert any(
        "Restored content mismatch: rustchain.db" in error
        for error in verifier.results["errors"]
    )
    assert not list(tmp_path.glob("rustchain_restore_*"))
