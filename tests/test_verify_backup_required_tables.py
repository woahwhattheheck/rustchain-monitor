import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "verify_backup.sh"


FAKE_SQLITE = r'''#!/usr/bin/env python3
import os
import sys
from pathlib import Path


db_path = Path(sys.argv[1])
sql = sys.argv[2]
case = os.environ.get("FAKE_BACKUP_CASE", "valid")

if sql == "PRAGMA integrity_check;":
    print("ok")
    raise SystemExit(0)

prefix = "SELECT COUNT(*) FROM "
if not sql.startswith(prefix) or not sql.endswith(";"):
    raise SystemExit(2)

table = sql[len(prefix):-1]
is_backup = db_path.name == "backup_test.db"

if table == "headers" and case == "missing":
    # The old script converted both failures to zero and then treated the
    # zero-vs-zero live delta as acceptable.
    raise SystemExit(1)

if table == "headers" and case == "empty":
    # The old script also accepted an empty required backup table whenever
    # the corresponding live count was zero.
    print("0")
    raise SystemExit(0)

print("5")
'''


def _run_verifier(tmp_path: Path, case: str) -> subprocess.CompletedProcess[str]:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "snapshot.bak").write_text("placeholder")
    live_db = tmp_path / "live.db"
    live_db.write_text("placeholder")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_sqlite = fake_bin / "sqlite3"
    fake_sqlite.write_text(FAKE_SQLITE)
    fake_sqlite.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(fake_bin), env.get("PATH", "")])
    env["FAKE_BACKUP_CASE"] = case

    return subprocess.run(
        ["bash", str(SCRIPT), str(backup_dir), str(live_db)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_required_table_query_failure_fails_closed_with_live_db(tmp_path):
    result = _run_verifier(tmp_path, "missing")

    assert result.returncode == 1
    assert "headers: ❌ (missing or unreadable in backup)" in result.stdout
    assert "RESULT: FAIL" in result.stdout


def test_empty_required_table_fails_closed_with_live_db(tmp_path):
    result = _run_verifier(tmp_path, "empty")

    assert result.returncode == 1
    assert "headers: 0 rows ❌ (empty or invalid!)" in result.stdout
    assert "RESULT: FAIL" in result.stdout


def test_nonempty_required_tables_still_pass(tmp_path):
    result = _run_verifier(tmp_path, "valid")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: PASS" in result.stdout
