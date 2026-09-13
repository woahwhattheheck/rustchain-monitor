import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "verify_backup.sh"


def _find_latest(backup_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; BACKUP_DIR="$2"; find_latest_backup',
            "bash",
            str(SCRIPT),
            str(backup_dir),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_preferred_rustchain_backup_wins_over_newer_generic_backup(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    preferred = backup_dir / "rustchain_v2-20260913.db.bak"
    generic = backup_dir / "unrelated-newer.bak"
    preferred.write_text("preferred")
    generic.write_text("generic")

    os.utime(preferred, (100, 100))
    os.utime(generic, (200, 200))

    result = _find_latest(backup_dir)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(preferred)


def test_generic_backup_is_fallback_when_preferred_pattern_is_absent(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    older = backup_dir / "older.bak"
    newer = backup_dir / "newer.bak"
    older.write_text("older")
    newer.write_text("newer")

    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))

    result = _find_latest(backup_dir)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(newer)
