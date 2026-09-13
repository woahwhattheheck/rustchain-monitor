import sys
from unittest.mock import patch

from backup_verify_lam1688 import BackupVerifier, main


def _run_main_with_result(tmp_path, result):
    argv = ["backup_verify_lam1688.py", str(tmp_path)]
    with patch.object(sys, "argv", argv):
        with patch.object(BackupVerifier, "run", return_value=result):
            return main()


def test_cli_returns_zero_for_clean_verification(tmp_path):
    result = {"errors": [], "integrity": True}

    assert _run_main_with_result(tmp_path, result) == 0


def test_cli_returns_nonzero_when_verification_records_errors(tmp_path):
    result = {"errors": ["Missing file: rustchain.db"], "integrity": True}

    assert _run_main_with_result(tmp_path, result) == 1


def test_cli_returns_nonzero_when_integrity_is_false(tmp_path):
    result = {"errors": [], "integrity": False}

    assert _run_main_with_result(tmp_path, result) == 1
