"""Bounded, paired measurement of the production SQLite comparison function.

This validation-only script is not part of the maintained test suite. It loads
both immutable baseline and candidate modules, creates one synthetic 30-day
history, and times complete calls including connection/schema initialization.
No node or other live-service endpoints are used.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time


BASE_COMMIT = "983e4331970e0d9cf34c962db5c8a19a2d0d7e89"
NOW = 2_100_000_000.0
MINERS = 32
SNAPSHOTS = 720
REPEATS = 100
DAYS = 7
ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_blob_sha(content):
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def compare(module, db_path, miner_ids):
    return module.compare_miner_history(
        db_path, miner_ids=miner_ids, now_ts=NOW, days=DAYS
    )


def trace_comparison(module, db_path, miner_ids):
    original = module._history_connection
    statements = []

    def connection(path):
        conn = original(path)
        conn.set_trace_callback(statements.append)
        return conn

    module._history_connection = connection
    try:
        result = compare(module, db_path, miner_ids)
    finally:
        module._history_connection = original
    selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
    return result, selects


def time_calls(module, db_path, miner_ids):
    started = time.perf_counter_ns()
    for _ in range(REPEATS):
        compare(module, db_path, miner_ids)
    return (time.perf_counter_ns() - started) / 1_000_000 / REPEATS


def main():
    baseline_bytes = subprocess.check_output(
        ["git", "show", BASE_COMMIT + ":rustchain_monitor.py"], cwd=ROOT
    )
    candidate_bytes = (ROOT / "rustchain_monitor.py").read_bytes()
    with tempfile.TemporaryDirectory(prefix="monitor59-comparison-") as temporary:
        temp = Path(temporary)
        baseline_path = temp / "baseline.py"
        baseline_path.write_bytes(baseline_bytes)
        baseline = load_module("monitor59_baseline", baseline_path)
        candidate = load_module("monitor59_candidate", ROOT / "rustchain_monitor.py")
        db_path = temp / "history.db"
        baseline.init_history_db(db_path)
        miner_ids = [f"synthetic-miner-{number:02d}" for number in range(MINERS)]
        with sqlite3.connect(db_path) as conn:
            conn.executemany(
                "INSERT INTO miner_history "
                "(miner_id, observed_at, epoch, balance_rtc, delta_rtc, device_arch, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (
                        miner,
                        NOW - (SNAPSHOTS - 1 - hour) * 3600,
                        hour // 24,
                        100.0 + number + hour * 0.003
                        - (3.0 if number % 8 == 0 and hour >= 700 else 0.0),
                        0.003,
                        "synthetic",
                        1,
                    )
                    for number, miner in enumerate(miner_ids)
                    for hour in range(SNAPSHOTS)
                ),
            )
        expected, baseline_queries = trace_comparison(baseline, db_path, miner_ids)
        actual, candidate_queries = trace_comparison(candidate, db_path, miner_ids)
        if actual != expected:
            raise AssertionError("Full comparison results differ")

        # Warm both paths before alternating their order. Tracing is removed.
        for _ in range(3):
            compare(baseline, db_path, miner_ids)
            compare(candidate, db_path, miner_ids)
        modules = {"baseline": baseline, "candidate": candidate}
        pairs = []
        for number, order in enumerate(
            [("baseline", "candidate"), ("candidate", "baseline"), ("baseline", "candidate")],
            start=1,
        ):
            times = {name: time_calls(modules[name], db_path, miner_ids) for name in order}
            pairs.append(
                {
                    "pair": number,
                    "order": list(order),
                    "milliseconds_per_full_call": times,
                    "candidate_reduction_percent":
                        (times["baseline"] - times["candidate"]) / times["baseline"] * 100,
                }
            )
        baseline_ms = statistics.median(
            pair["milliseconds_per_full_call"]["baseline"] for pair in pairs
        )
        candidate_ms = statistics.median(
            pair["milliseconds_per_full_call"]["candidate"] for pair in pairs
        )
        evidence = {
            "base_commit": BASE_COMMIT,
            "candidate_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "base_source_blob": git_blob_sha(baseline_bytes),
            "candidate_source_blob": git_blob_sha(candidate_bytes),
            "runtime": {
                "python": sys.version,
                "sqlite": sqlite3.sqlite_version,
                "platform": platform.system(),
                "machine": platform.machine(),
            },
            "fixture": {
                "miners": MINERS,
                "snapshots_per_miner": SNAPSHOTS,
                "total_snapshots": MINERS * SNAPSHOTS,
                "interval_seconds": 3600,
                "comparison_days": DAYS,
                "now_ts": NOW,
                "sqlite_bytes": db_path.stat().st_size,
                "synthetic": True,
            },
            "full_result_equal": actual == expected,
            "baseline_result": expected,
            "candidate_result": actual,
            "selects_per_comparison": {
                "baseline": len(baseline_queries),
                "candidate": len(candidate_queries),
            },
            "baseline_first_miner_selects": baseline_queries[:3],
            "candidate_first_miner_selects": candidate_queries[:2],
            "warmup_calls_per_version": 3,
            "calls_per_timed_batch": REPEATS,
            "pairs": pairs,
            "median_batch_mean_ms_per_full_call": {
                "baseline": baseline_ms,
                "candidate": candidate_ms,
            },
            "median_reduction_percent": (baseline_ms - candidate_ms) / baseline_ms * 100,
            "limits": "One bounded synthetic SQLite fixture; three alternating pairs; "
            "full production calls include schema initialization and connection cost; "
            "no memory, live-node, cold-disk, or fleet-wide throughput claim.",
        }
        output = json.dumps(evidence, indent=2, sort_keys=True)
        print("MONITOR59_QUERY_REUSE_EVIDENCE_BEGIN")
        print(output)
        print("MONITOR59_QUERY_REUSE_EVIDENCE_END")
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a") as summary:
                summary.write("## Monitor59 production comparison measurement\n\n")
                summary.write(f"Full result equality: {actual == expected}. ")
                summary.write(f"SELECT statements: {len(baseline_queries)} → {len(candidate_queries)}.\n\n")
                summary.write(f"Median batch mean: {baseline_ms:.6f} → {candidate_ms:.6f} ms per call.\n\n")
                summary.write("Raw results are in this step's log; no live-node benchmark was run.\n")


if __name__ == "__main__":
    main()
