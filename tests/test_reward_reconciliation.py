import copy
import json
import os
import subprocess
import sys
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reward_reconciliation import (  # noqa: E402
    ReconciliationError,
    SOURCE_SCHEMA,
    compile_reconciliation,
    load_json_strict,
    normalize_source,
    source_from_history_db,
    verify_reconciliation,
)


def source(*rows, miner_id="miner-alpha"):
    return {
        "schema_version": SOURCE_SCHEMA,
        "miner_id": miner_id,
        "observations": list(rows),
    }


def row(ts, epoch, balance):
    return {"observed_at": ts, "epoch": epoch, "balance_rtc": balance}


class RewardReconciliationTests(unittest.TestCase):
    def healthy_source(self):
        return source(
            row("2026-09-13T09:00:00Z", 100, "10.000000"),
            row("2026-09-13T09:10:00Z", 101, "10.375"),
            row("2026-09-13T09:20:00Z", 102, Decimal("10.7500")),
        )

    def test_deterministic_and_canonical_decimals(self):
        first = compile_reconciliation(self.healthy_source())
        second = compile_reconciliation(self.healthy_source())
        self.assertEqual(first, second)
        self.assertEqual(first["report"]["transitions"][0]["from_balance_rtc"], "10")
        self.assertEqual(first["report"]["transitions"][0]["balance_delta_rtc"], "0.375")
        self.assertTrue(verify_reconciliation(self.healthy_source(), first))

    def test_positive_progress_is_info(self):
        artifact = compile_reconciliation(self.healthy_source())
        transition = artifact["report"]["transitions"][0]
        self.assertEqual(transition["severity"], "INFO")
        self.assertEqual(transition["signals"], ["POSITIVE_OBSERVED_GAIN"])

    def test_epoch_advance_without_gain_is_caution(self):
        artifact = compile_reconciliation(source(
            row("2026-09-13T09:00:00Z", 100, "10"),
            row("2026-09-13T09:10:00Z", 101, "10"),
        ))
        transition = artifact["report"]["transitions"][0]
        self.assertEqual(transition["severity"], "CAUTION")
        self.assertIn("EPOCH_ADVANCE_NO_OBSERVED_GAIN", transition["signals"])

    def test_gap_is_caution_without_expected_reward_inference(self):
        artifact = compile_reconciliation(source(
            row("2026-09-13T09:00:00Z", 100, "10"),
            row("2026-09-13T09:40:00Z", 104, "10.9"),
        ))
        transition = artifact["report"]["transitions"][0]
        self.assertEqual(transition["severity"], "CAUTION")
        self.assertEqual(transition["signals"], ["OBSERVATION_GAP", "POSITIVE_OBSERVED_GAIN"])
        self.assertFalse(artifact["report"]["authority"]["expected_reward_inferred"])

    def test_epoch_regression_is_anomaly(self):
        artifact = compile_reconciliation(source(
            row("2026-09-13T09:00:00Z", 100, "10"),
            row("2026-09-13T09:10:00Z", 99, "10.1"),
        ))
        transition = artifact["report"]["transitions"][0]
        self.assertEqual(transition["severity"], "ANOMALY")
        self.assertIn("EPOCH_REGRESSION", transition["signals"])

    def test_balance_regression_is_anomaly(self):
        artifact = compile_reconciliation(source(
            row("2026-09-13T09:00:00Z", 100, "10"),
            row("2026-09-13T09:10:00Z", 101, "9.75"),
        ))
        transition = artifact["report"]["transitions"][0]
        self.assertEqual(transition["severity"], "ANOMALY")
        self.assertIn("BALANCE_REGRESSION", transition["signals"])
        self.assertEqual(transition["balance_delta_rtc"], "-0.25")

    def test_same_epoch_balance_change_is_anomaly(self):
        artifact = compile_reconciliation(source(
            row("2026-09-13T09:00:00Z", 100, "10"),
            row("2026-09-13T09:01:00Z", 100, "10.1"),
        ))
        transition = artifact["report"]["transitions"][0]
        self.assertEqual(transition["severity"], "ANOMALY")
        self.assertIn("SAME_EPOCH_BALANCE_CONFLICT", transition["signals"])

    def test_same_epoch_stable_is_info(self):
        artifact = compile_reconciliation(source(
            row("2026-09-13T09:00:00Z", 100, "10"),
            row("2026-09-13T09:01:00Z", 100, "10"),
        ))
        self.assertEqual(artifact["report"]["transitions"][0]["signals"], ["SAME_EPOCH_STABLE"])

    def test_cross_miner_identity_is_bound_into_digest(self):
        one = compile_reconciliation(self.healthy_source())
        two = compile_reconciliation(source(*self.healthy_source()["observations"], miner_id="miner-beta"))
        self.assertNotEqual(one["report"]["source_sha256"], two["report"]["source_sha256"])
        self.assertFalse(verify_reconciliation(source(*self.healthy_source()["observations"], miner_id="miner-beta"), one))

    def test_report_tamper_fails_full_recompile(self):
        artifact = compile_reconciliation(self.healthy_source())
        tampered = copy.deepcopy(artifact)
        tampered["report"]["summary"]["anomaly_transition_count"] = 999
        self.assertFalse(verify_reconciliation(self.healthy_source(), tampered))

    def test_markdown_tamper_fails_full_recompile(self):
        artifact = compile_reconciliation(self.healthy_source())
        tampered = copy.deepcopy(artifact)
        tampered["markdown"] += "forged\n"
        self.assertFalse(verify_reconciliation(self.healthy_source(), tampered))

    def test_receipt_tamper_fails_full_recompile(self):
        artifact = compile_reconciliation(self.healthy_source())
        tampered = copy.deepcopy(artifact)
        tampered["receipt"]["report_sha256"] = "0" * 64
        self.assertFalse(verify_reconciliation(self.healthy_source(), tampered))

    def test_reordered_observations_fail_closed(self):
        data = self.healthy_source()
        data["observations"] = [data["observations"][1], data["observations"][0]]
        with self.assertRaises(ReconciliationError):
            normalize_source(data)

    def test_duplicate_timestamp_fails_closed(self):
        with self.assertRaises(ReconciliationError):
            normalize_source(source(
                row("2026-09-13T09:00:00Z", 100, "10"),
                row("2026-09-13T09:00:00Z", 101, "10.1"),
            ))

    def test_bool_epoch_fails_closed(self):
        with self.assertRaises(ReconciliationError):
            normalize_source(source(
                row("2026-09-13T09:00:00Z", 100, "10"),
                row("2026-09-13T09:10:00Z", True, "10.1"),
            ))

    def test_bool_balance_fails_closed(self):
        with self.assertRaises(ReconciliationError):
            normalize_source(source(
                row("2026-09-13T09:00:00Z", 100, "10"),
                row("2026-09-13T09:10:00Z", 101, False),
            ))

    def test_nonfinite_balance_fails_closed(self):
        for bad in (float("nan"), float("inf"), Decimal("Infinity"), "NaN"):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ReconciliationError):
                    normalize_source(source(
                        row("2026-09-13T09:00:00Z", 100, "10"),
                        row("2026-09-13T09:10:00Z", 101, bad),
                    ))

    def test_unknown_fields_fail_closed(self):
        data = self.healthy_source()
        data["observations"][0]["expected_reward"] = "0.375"
        with self.assertRaises(ReconciliationError):
            normalize_source(data)

    def test_fractional_utc_is_canonicalized_but_offsets_fail_closed(self):
        normalized = normalize_source(source(
            row("2026-09-13T09:00:00Z", 100, "10"),
            row("2026-09-13T09:10:00.001000Z", 101, "10.1"),
        ))
        self.assertEqual(normalized["observations"][1]["observed_at"], "2026-09-13T09:10:00.001Z")
        for bad in ("2026-09-13T09:10:00.1234567Z", "2026-09-13T05:10:00-04:00", "not-a-time"):
            with self.subTest(bad=bad):
                with self.assertRaises(ReconciliationError):
                    normalize_source(source(
                        row("2026-09-13T09:00:00Z", 100, "10"),
                        row(bad, 101, "10.1"),
                    ))

    def test_history_db_adapter_is_read_only_and_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "history.db"
            conn = sqlite3.connect(db)
            conn.executescript(
                """
                CREATE TABLE miner_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    miner_id TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    epoch INTEGER,
                    balance_rtc REAL NOT NULL,
                    delta_rtc REAL DEFAULT 0,
                    device_arch TEXT DEFAULT '',
                    is_active INTEGER DEFAULT 0
                );
                """
            )
            conn.executemany(
                "INSERT INTO miner_history (miner_id, observed_at, epoch, balance_rtc) VALUES (?, ?, ?, ?)",
                [
                    ("miner-alpha", 1789290000.125, 100, 10.0),
                    ("miner-alpha", 1789290600.5, 101, 10.375),
                    ("miner-beta", 1789290600.5, 101, 2.0),
                ],
            )
            conn.commit()
            conn.close()
            first = source_from_history_db(db, "miner-alpha")
            second = source_from_history_db(db, "miner-alpha")
            self.assertEqual(first, second)
            self.assertEqual(first["miner_id"], "miner-alpha")
            self.assertEqual(len(first["observations"]), 2)
            self.assertTrue(first["observations"][0]["observed_at"].endswith("Z"))
            artifact = compile_reconciliation(first)
            self.assertTrue(verify_reconciliation(first, artifact))

    def test_history_db_missing_epoch_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "history.db"
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE miner_history (id INTEGER PRIMARY KEY, miner_id TEXT, observed_at REAL, epoch INTEGER, balance_rtc REAL)"
            )
            conn.executemany(
                "INSERT INTO miner_history VALUES (?, ?, ?, ?, ?)",
                [(1, "m", 1.0, 1, 1.0), (2, "m", 2.0, None, 1.1)],
            )
            conn.commit()
            conn.close()
            with self.assertRaises(ReconciliationError):
                source_from_history_db(db, "m")

    def test_duplicate_json_keys_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "dup.json"
            path.write_text(
                '{"schema_version":"rustchain.reward-observations/v1",'
                '"miner_id":"a","miner_id":"b","observations":[]}',
                encoding="utf-8",
            )
            with self.assertRaises(ReconciliationError):
                load_json_strict(path)

    def test_cli_compile_verify_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "source.json"
            out = td / "artifact.json"
            md = td / "artifact.md"
            src.write_text(json.dumps({
                "schema_version": SOURCE_SCHEMA,
                "miner_id": "miner-alpha",
                "observations": [
                    row("2026-09-13T09:00:00Z", 100, "10"),
                    row("2026-09-13T09:10:00Z", 101, "10.375"),
                ],
            }), encoding="utf-8")
            compile_run = subprocess.run(
                [sys.executable, str(ROOT / "reward_reconciliation.py"), "compile", str(src), "--json-out", str(out), "--markdown-out", str(md)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(compile_run.returncode, 0, compile_run.stderr)
            verify_run = subprocess.run(
                [sys.executable, str(ROOT / "reward_reconciliation.py"), "verify", str(src), str(out)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(verify_run.returncode, 0, verify_run.stderr)
            self.assertEqual(json.loads(verify_run.stdout), {"ok": True})
            overwrite = subprocess.run(
                [sys.executable, str(ROOT / "reward_reconciliation.py"), "compile", str(src), "--json-out", str(out), "--markdown-out", str(md)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(overwrite.returncode, 2)
            self.assertIn("refusing to overwrite", overwrite.stderr)

    def test_authority_flags_are_all_false(self):
        artifact = compile_reconciliation(self.healthy_source())
        self.assertTrue(artifact["report"]["authority"])
        self.assertTrue(all(value is False for value in artifact["report"]["authority"].values()))


if __name__ == "__main__":
    unittest.main()
