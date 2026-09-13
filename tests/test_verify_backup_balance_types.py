import sqlite3

import verify_backup


def _create_required_tables(conn):
    conn.execute("CREATE TABLE miner_attest_recent (id INTEGER)")
    conn.execute("CREATE TABLE headers (id INTEGER)")
    conn.execute("CREATE TABLE ledger (id INTEGER)")
    conn.execute("CREATE TABLE epoch_rewards (id INTEGER)")
    conn.execute("INSERT INTO headers (id) VALUES (1)")


def test_nonnumeric_text_cannot_prove_positive_balance(tmp_path):
    db = tmp_path / "malformed-balance.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE balances (amount_i64 INTEGER)")
    _create_required_tables(conn)
    # SQLite allows dynamic storage types even under INTEGER affinity. Before
    # the fix this TEXT value satisfied `amount_i64 > 0` by storage-class
    # ordering and made a corrupted balance table look positive.
    conn.execute("INSERT INTO balances (amount_i64) VALUES (?)", ("oops",))
    conn.commit()
    conn.close()

    info = verify_backup.get_table_info(str(db), "balances")
    passed, results = verify_backup.verify_tables(str(db), None)

    assert info == {"exists": True, "row_count": 1, "has_positive": False}
    assert passed is False
    assert any("balances: 1 rows" in result and "❌" in result for result in results)


def test_real_sqlite_numeric_values_still_prove_positive_balance(tmp_path):
    integer_db = tmp_path / "integer-balance.db"
    conn = sqlite3.connect(integer_db)
    conn.execute("CREATE TABLE balances (amount_i64 INTEGER)")
    conn.execute("INSERT INTO balances (amount_i64) VALUES (7)")
    conn.commit()
    conn.close()

    real_db = tmp_path / "real-balance.db"
    conn = sqlite3.connect(real_db)
    conn.execute("CREATE TABLE balances (amount REAL)")
    conn.execute("INSERT INTO balances (amount) VALUES (2.5)")
    conn.commit()
    conn.close()

    assert verify_backup.get_table_info(str(integer_db), "balances")["has_positive"] is True
    assert verify_backup.get_table_info(str(real_db), "balances")["has_positive"] is True
