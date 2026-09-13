"""POSIX durability regressions for reporter-state publication."""

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import epoch_reporter


@pytest.mark.skipif(epoch_reporter.os.name == "nt", reason="directory fsync is POSIX-only")
def test_save_state_syncs_parent_directory_after_replace(tmp_path, monkeypatch):
    state_file = tmp_path / "reporter-state.json"
    state_file.write_text('{"last_epoch": 41}\n', encoding="utf-8")
    events = []
    real_replace = epoch_reporter.os.replace
    real_open = epoch_reporter.os.open
    real_fsync = epoch_reporter.os.fsync
    real_close = epoch_reporter.os.close

    def tracked_replace(src, dst):
        events.append(("replace", pathlib.Path(dst)))
        return real_replace(src, dst)

    def tracked_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        events.append(("open", pathlib.Path(path), fd))
        return fd

    def tracked_fsync(fd):
        events.append(("fsync", fd))
        return real_fsync(fd)

    def tracked_close(fd):
        events.append(("close", fd))
        return real_close(fd)

    monkeypatch.setattr(epoch_reporter.os, "replace", tracked_replace)
    monkeypatch.setattr(epoch_reporter.os, "open", tracked_open)
    monkeypatch.setattr(epoch_reporter.os, "fsync", tracked_fsync)
    monkeypatch.setattr(epoch_reporter.os, "close", tracked_close)

    state = {"last_epoch": 42, "tracked_miners": {}}
    epoch_reporter.save_state(str(state_file), state)

    replace_i = next(i for i, event in enumerate(events) if event[0] == "replace")
    dir_open_i = next(
        i
        for i, event in enumerate(events)
        if i > replace_i and event[0] == "open" and event[1] == tmp_path
    )
    dir_fd = events[dir_open_i][2]
    sync_i = next(
        i
        for i, event in enumerate(events)
        if i > dir_open_i and event == ("fsync", dir_fd)
    )
    close_i = next(
        i
        for i, event in enumerate(events)
        if i > sync_i and event == ("close", dir_fd)
    )

    assert replace_i < dir_open_i < sync_i < close_i
    assert json.loads(state_file.read_text(encoding="utf-8")) == state


@pytest.mark.skipif(epoch_reporter.os.name == "nt", reason="directory fsync is POSIX-only")
def test_parent_directory_sync_closes_fd_when_fsync_fails(tmp_path, monkeypatch):
    state_file = tmp_path / "reporter-state.json"
    closed = []
    monkeypatch.setattr(epoch_reporter.os, "open", lambda *args, **kwargs: 123)

    def fail_fsync(fd):
        raise OSError("simulated directory sync failure")

    monkeypatch.setattr(epoch_reporter.os, "fsync", fail_fsync)
    monkeypatch.setattr(epoch_reporter.os, "close", closed.append)

    with pytest.raises(OSError, match="simulated directory sync failure"):
        epoch_reporter._sync_parent_directory(state_file)

    assert closed == [123]
