import csv
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rustchain_monitor


def _seed_history(db_path: Path) -> None:
    rustchain_monitor.record_history_snapshot(
        db_path,
        miner_id="miner-custody",
        epoch=401,
        balance_rtc=3.5,
        observed_at=1500.0,
    )


def test_history_csv_replaces_final_symlink_without_touching_target(tmp_path):
    db_path = tmp_path / "history.db"
    _seed_history(db_path)

    outside = tmp_path / "outside.csv"
    outside.write_text("sentinel\n")
    export_path = tmp_path / "export.csv"
    export_path.symlink_to(outside)

    row_count = rustchain_monitor.export_history_csv(
        db_path,
        miner_id="miner-custody",
        csv_path=export_path,
    )

    assert row_count == 1
    assert outside.read_text() == "sentinel\n"
    assert export_path.is_file()
    assert not export_path.is_symlink()
    with open(export_path, newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[1][2] == "miner-custody"
    assert rows[1][4] == "3.5"


def test_history_csv_rejects_symlinked_parent_without_external_write(tmp_path):
    db_path = tmp_path / "history.db"
    _seed_history(db_path)

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="output parent must not traverse links"):
        rustchain_monitor.export_history_csv(
            db_path,
            miner_id="miner-custody",
            csv_path=linked_parent / "export.csv",
        )

    assert not (outside_dir / "export.csv").exists()


def test_history_csv_still_overwrites_regular_destination(tmp_path):
    db_path = tmp_path / "history.db"
    _seed_history(db_path)

    export_path = tmp_path / "export.csv"
    export_path.write_text("old-data\n")

    row_count = rustchain_monitor.export_history_csv(
        db_path,
        miner_id="miner-custody",
        csv_path=export_path,
    )

    assert row_count == 1
    assert "old-data" not in export_path.read_text()
    with open(export_path, newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0][0] == "observed_at_iso"
    assert rows[1][2] == "miner-custody"
