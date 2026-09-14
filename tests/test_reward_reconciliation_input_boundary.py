import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reward_reconciliation import (  # noqa: E402
    MAX_JSON_BYTES,
    ReconciliationError,
    SOURCE_SCHEMA,
    load_json_strict,
)


class RewardReconciliationInputBoundaryTests(unittest.TestCase):
    @staticmethod
    def valid_source() -> dict:
        return {
            "schema_version": SOURCE_SCHEMA,
            "miner_id": "miner-alpha",
            "observations": [
                {"observed_at": "2026-09-13T09:00:00Z", "epoch": 100, "balance_rtc": "10"},
                {"observed_at": "2026-09-13T09:10:00Z", "epoch": 101, "balance_rtc": "10.5"},
            ],
        }

    def test_regular_utf8_json_still_loads(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source.json"
            expected = self.valid_source()
            path.write_text(json.dumps(expected), encoding="utf-8")
            self.assertEqual(load_json_strict(path), expected)

    def test_final_component_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "source.json"
            link = root / "source-link.json"
            target.write_text(json.dumps(self.valid_source()), encoding="utf-8")
            try:
                link.symlink_to(target.name)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            with self.assertRaisesRegex(ReconciliationError, "non-symlink"):
                load_json_strict(link)

    def test_fifo_fails_closed_without_opening_stream(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO creation unavailable")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source.pipe"
            os.mkfifo(path)
            with self.assertRaisesRegex(ReconciliationError, "regular file"):
                load_json_strict(path)

    def test_regular_path_swapped_to_fifo_cannot_block_open(self):
        if not hasattr(os, "mkfifo") or not getattr(os, "O_NONBLOCK", 0):
            self.skipTest("nonblocking FIFO open unavailable")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source.json"
            path.write_text(json.dumps(self.valid_source()), encoding="utf-8")
            original_open = os.open
            swapped = False

            def swap_to_fifo_then_open(target, flags, *args, **kwargs):
                nonlocal swapped
                if not swapped:
                    swapped = True
                    path.unlink()
                    os.mkfifo(path)
                    self.assertTrue(flags & os.O_NONBLOCK)
                return original_open(target, flags, *args, **kwargs)

            with patch("reward_reconciliation.os.open", side_effect=swap_to_fifo_then_open):
                with self.assertRaisesRegex(ReconciliationError, "regular file"):
                    load_json_strict(path)

    def test_oversized_sparse_file_fails_before_parse(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "oversized.json"
            with path.open("wb") as handle:
                handle.truncate(MAX_JSON_BYTES + 1)
            with self.assertRaisesRegex(ReconciliationError, "safety bound"):
                load_json_strict(path)

    def test_path_swap_before_descriptor_binding_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "source.json"
            replacement = root / "replacement.json"
            path.write_text(json.dumps(self.valid_source()), encoding="utf-8")
            changed = self.valid_source()
            changed["miner_id"] = "miner-beta"
            replacement.write_text(json.dumps(changed), encoding="utf-8")
            original_open = os.open
            swapped = False

            def swap_then_open(target, flags, *args, **kwargs):
                nonlocal swapped
                if not swapped:
                    swapped = True
                    replacement.replace(path)
                return original_open(target, flags, *args, **kwargs)

            with patch("reward_reconciliation.os.open", side_effect=swap_then_open):
                with self.assertRaisesRegex(ReconciliationError, "descriptor binding"):
                    load_json_strict(path)

    def test_mutation_during_descriptor_read_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source.json"
            path.write_text(json.dumps(self.valid_source()), encoding="utf-8")
            original_read = os.read
            mutated = False

            def mutate_after_first_read(fd: int, count: int) -> bytes:
                nonlocal mutated
                chunk = original_read(fd, count)
                if chunk and not mutated:
                    mutated = True
                    with path.open("ab") as handle:
                        handle.write(b" ")
                return chunk

            with patch("reward_reconciliation.os.read", side_effect=mutate_after_first_read):
                with self.assertRaisesRegex(ReconciliationError, "changed while being read"):
                    load_json_strict(path)

    def test_invalid_utf8_is_normalized_to_reconciliation_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source.json"
            path.write_bytes(b"{\xff}")
            with self.assertRaisesRegex(ReconciliationError, "valid UTF-8"):
                load_json_strict(path)


if __name__ == "__main__":
    unittest.main()
