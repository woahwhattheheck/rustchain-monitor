import os
from pathlib import Path

from verify_backup import find_latest_backup


def _set_mtime(path: Path, timestamp: int) -> None:
    os.utime(path, (timestamp, timestamp))


def test_preferred_pattern_beats_newer_generic_backup(tmp_path):
    preferred = tmp_path / "rustchain_v2-20260913.db.bak"
    generic = tmp_path / "unrelated-newer.db.bak"
    preferred.write_text("preferred")
    generic.write_text("generic")
    _set_mtime(preferred, 100)
    _set_mtime(generic, 200)

    assert find_latest_backup(str(tmp_path)) == str(preferred)


def test_newest_file_wins_within_first_matching_pattern(tmp_path):
    older = tmp_path / "rustchain_v2-older.db.bak"
    newer = tmp_path / "rustchain_v2-newer.db.bak"
    older.write_text("older")
    newer.write_text("newer")
    _set_mtime(older, 100)
    _set_mtime(newer, 200)

    assert find_latest_backup(str(tmp_path)) == str(newer)


def test_generic_pattern_remains_fallback(tmp_path):
    generic = tmp_path / "other.db.bak"
    generic.write_text("generic")

    assert find_latest_backup(str(tmp_path)) == str(generic)
