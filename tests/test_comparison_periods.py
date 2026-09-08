"""Comparison windows must match the number of days requested by the caller."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rustchain_monitor import compare_miner_history, record_history_snapshot


NOW = 2_100_000_000.0
DAY = 86400


def record(db_path, miner, age, balance, epoch):
    record_history_snapshot(
        db_path,
        miner_id=miner,
        epoch=epoch,
        balance_rtc=balance,
        observed_at=NOW - age * DAY,
    )


@pytest.mark.parametrize(
    "days,old_gain_age,expected_old_gain,first_miner",
    [
        (2, 4, 0.0, "recent"),
        (14, 20, 0.0, "recent"),
        (60, 50, 100.0, "old"),
        (1, 4, 0.0, "recent"),
        (7, 4, 100.0, "old"),
        (30, 20, 100.0, "old"),
    ],
)
def test_comparison_uses_exact_requested_window(
    tmp_path, days, old_gain_age, expected_old_gain, first_miner
):
    db_path = tmp_path / "history.db"
    for miner in ("old", "recent"):
        record(db_path, miner, 100, 0.0, 1)
    record(db_path, "old", old_gain_age, 100.0, 2)
    record(db_path, "old", 0, 100.0, 3)
    record(db_path, "recent", 0.5, 10.0, 2)

    rows = compare_miner_history(
        db_path, miner_ids=["old", "recent"], now_ts=NOW, days=days
    )

    assert rows[0]["miner_id"] == first_miner
    by_miner = {row["miner_id"]: row for row in rows}
    assert by_miner["old"]["recent_gain"] == expected_old_gain
    assert by_miner["recent"]["recent_gain"] == 10.0
    assert by_miner["old"]["daily_average"] == pytest.approx(expected_old_gain / days)
    assert by_miner["recent"]["daily_average"] == pytest.approx(10.0 / days)


def test_custom_window_preserves_empty_history(tmp_path):
    rows = compare_miner_history(
        tmp_path / "history.db", miner_ids=["missing"], now_ts=NOW, days=14
    )
    assert rows[0]["recent_gain"] == 0.0
    assert rows[0]["daily_average"] == 0.0
    assert rows[0]["snapshots"] == 0
