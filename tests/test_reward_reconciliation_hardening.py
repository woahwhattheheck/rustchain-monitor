import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reward_reconciliation import (  # noqa: E402
    ReconciliationError,
    SOURCE_SCHEMA,
    normalize_source,
    source_from_history_db,
)


def source(balance="10.1", epoch=101):
    return {
        "schema_version": SOURCE_SCHEMA,
        "miner_id": "miner-alpha",
        "observations": [
            {"observed_at": "2026-09-13T09:00:00Z", "epoch": 100, "balance_rtc": "10"},
            {"observed_at": "2026-09-13T09:10:00Z", "epoch": epoch, "balance_rtc": balance},
        ],
    }


class RewardReconciliationHardeningTests(unittest.TestCase):
    def test_extreme_finite_decimal_and_epoch_fail_closed(self):
        for bad in ("1e1000000000", "0." + ("1" * 300)):
            with self.subTest(balance=bad), self.assertRaises(ReconciliationError):
                normalize_source(source(balance=bad))
        with self.assertRaises(ReconciliationError):
            normalize_source(source(epoch=1 << 63))

    def test_history_db_uri_quotes_special_path_characters(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "history?special#name.db"
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE miner_history (id INTEGER PRIMARY KEY, miner_id TEXT, observed_at REAL, epoch INTEGER, balance_rtc REAL)"
            )
            conn.executemany(
                "INSERT INTO miner_history VALUES (?, ?, ?, ?, ?)",
                [(1, "m", 1.0, 1, 1.0), (2, "m", 2.0, 2, 1.1)],
            )
            conn.commit()
            conn.close()
            compiled = source_from_history_db(db, "m")
            self.assertEqual(len(compiled["observations"]), 2)

    def test_cli_rejects_symlinked_output_parent_chain(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            real = td / "real"
            real.mkdir()
            linked = td / "linked"
            try:
                os.symlink(real, linked, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            src = td / "source.json"
            src.write_text(json.dumps(source()), encoding="utf-8")
            run = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "reward_reconciliation.py"),
                    "compile",
                    str(src),
                    "--json-out",
                    str(linked / "report.json"),
                    "--markdown-out",
                    str(td / "report.md"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run.returncode, 2)
            self.assertIn("must not traverse links", run.stderr)
            self.assertFalse((real / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
