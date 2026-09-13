import sqlite3

import pytest

import verify_backup


REQUIRED_DATA_TABLES = (
    "miner_attest_recent",
    "ledger",
    "epoch_rewards",
)


def _make_valid_backup(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE balances (amount_i64 INTEGER);
        CREATE TABLE miner_attest_recent (id INTEGER);
        CREATE TABLE headers (id INTEGER);
        CREATE TABLE ledger (id INTEGER);
        CREATE TABLE epoch_rewards (id INTEGER);

        INSERT INTO balances VALUES (1);
        INSERT INTO miner_attest_recent VALUES (1);
        INSERT INTO headers VALUES (1);
        INSERT INTO ledger VALUES (1);
        INSERT INTO epoch_rewards VALUES (1);
        """
    )
    conn.commit()
    conn.close()


def test_populated_required_tables_pass_without_live_comparison(tmp_path):
    backup = tmp_path / "backup.db"
    _make_valid_backup(backup)

    passed, results = verify_backup.verify_tables(str(backup), None)

    assert passed is True
    assert len(results) == len(verify_backup.REQUIRED_TABLES)
    assert all(result.endswith("✅") for result in results)


@pytest.mark.parametrize("table", REQUIRED_DATA_TABLES)
def test_empty_required_data_table_fails_verification(tmp_path, table):
    backup = tmp_path / "backup.db"
    _make_valid_backup(backup)

    conn = sqlite3.connect(backup)
    conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()

    passed, results = verify_backup.verify_tables(str(backup), None)

    assert passed is False
    result = next(line for line in results if line.strip().startswith(f"{table}:"))
    assert "0 rows" in result
    assert result.endswith("❌")


def test_required_data_minimums_match_bounty_contract():
    for table in REQUIRED_DATA_TABLES:
        assert verify_backup.REQUIRED_TABLES[table]["min_rows"] == 1
