"""Atomic reporter-state persistence regressions."""

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import epoch_reporter


def test_save_state_preserves_existing_file_when_write_fails(tmp_path, monkeypatch):
    state_file = tmp_path / "reporter-state.json"
    original = '{"last_epoch": 41}\n'
    state_file.write_text(original, encoding="utf-8")

    def interrupted_dump(state, handle, *, indent, sort_keys):
        del state, indent, sort_keys
        handle.write('{"last_epoch":')
        handle.flush()
        raise OSError("simulated interrupted state write")

    monkeypatch.setattr(epoch_reporter.json, "dump", interrupted_dump)

    with pytest.raises(OSError, match="simulated interrupted state write"):
        epoch_reporter.save_state(str(state_file), {"last_epoch": 42})

    assert state_file.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(f".{state_file.name}.*.tmp")) == []


def test_save_state_atomically_replaces_existing_file(tmp_path):
    state_file = tmp_path / "reporter-state.json"
    state_file.write_text('{"last_epoch": 41}\n', encoding="utf-8")
    state = {"last_epoch": 42, "tracked_miners": {}}

    epoch_reporter.save_state(str(state_file), state)

    assert json.loads(state_file.read_text(encoding="utf-8")) == state
    assert list(tmp_path.glob(f".{state_file.name}.*.tmp")) == []
