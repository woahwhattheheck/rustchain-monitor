import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reward_casebook import CasebookError, _write_outputs, compile_casebook  # noqa: E402
from reward_reconciliation import SOURCE_SCHEMA, compile_reconciliation  # noqa: E402


def _source(rows, miner_id="miner-chronology"):
    return {
        "schema_version": SOURCE_SCHEMA,
        "miner_id": miner_id,
        "observations": rows,
    }


def _row(ts, epoch, balance):
    return {"observed_at": ts, "epoch": epoch, "balance_rtc": balance}


def _evidence(rows, miner_id="miner-chronology"):
    source = _source(rows, miner_id=miner_id)
    return source, compile_reconciliation(source)


def test_info_only_future_evidence_is_rejected_by_as_of_snapshot():
    pair = _evidence([
        _row("2026-09-13T11:00:00Z", 100, "10"),
        _row("2026-09-13T11:10:00Z", 101, "10.5"),
    ])
    assert pair[1]["report"]["summary"]["info_transition_count"] == 1

    with pytest.raises(CasebookError, match="contains observations after as_of"):
        compile_casebook([pair], as_of="2026-09-13T10:00:00Z")


def test_material_case_cannot_hide_later_info_observation_after_as_of():
    pair = _evidence([
        _row("2026-09-13T09:00:00Z", 200, "20"),
        _row("2026-09-13T09:10:00Z", 201, "20"),
        _row("2026-09-13T11:10:00Z", 202, "20.5"),
    ])
    report = pair[1]["report"]
    assert report["transitions"][0]["severity"] == "CAUTION"
    assert report["transitions"][1]["severity"] == "INFO"
    assert report["last_observed_at"] == "2026-09-13T11:10:00Z"

    with pytest.raises(CasebookError, match="contains observations after as_of"):
        compile_casebook([pair], as_of="2026-09-13T10:00:00Z")


def test_evidence_exactly_at_as_of_remains_valid():
    pair = _evidence([
        _row("2026-09-13T09:50:00Z", 300, "30"),
        _row("2026-09-13T10:00:00Z", 301, "30.1"),
    ])
    artifact = compile_casebook([pair], as_of="2026-09-13T10:00:00Z")
    assert artifact["casebook"]["summary"]["case_count"] == 0


def test_output_pair_preflight_does_not_leave_partial_json_when_markdown_exists():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        json_out = root / "casebook.json"
        markdown_out = root / "casebook.md"
        markdown_out.write_text("existing\n", encoding="utf-8")

        with pytest.raises(CasebookError, match="refusing to overwrite existing file"):
            _write_outputs(json_out, "{}\n", markdown_out, "# report\n")

        assert not json_out.exists()
        assert markdown_out.read_text(encoding="utf-8") == "existing\n"


def test_output_pair_rolls_back_first_file_if_second_create_races(monkeypatch):
    import reward_casebook as mod

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        json_out = root / "casebook.json"
        markdown_out = root / "casebook.md"
        real_write = mod._write_new
        calls = []

        def racing_write(path, text):
            calls.append(Path(path))
            if len(calls) == 2:
                Path(path).write_text("racer\n", encoding="utf-8")
            return real_write(path, text)

        monkeypatch.setattr(mod, "_write_new", racing_write)
        with pytest.raises(CasebookError, match="refusing to overwrite existing file"):
            mod._write_outputs(json_out, json.dumps({"ok": True}), markdown_out, "# report\n")

        assert not json_out.exists()
        assert markdown_out.read_text(encoding="utf-8") == "racer\n"
