import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import epoch_reporter


def test_load_config_accepts_mapping_root(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"node": "https://node.example", "offline_polls": 3}')

    assert epoch_reporter.load_config(str(config_path)) == {
        "node": "https://node.example",
        "offline_polls": 3,
    }


@pytest.mark.parametrize("payload", ["[]", "null", '"string"', "1"])
def test_load_config_rejects_non_mapping_json_root(tmp_path, payload):
    config_path = tmp_path / "config.json"
    config_path.write_text(payload)

    with pytest.raises(ValueError, match="config root must be a JSON object"):
        epoch_reporter.load_config(str(config_path))


@pytest.mark.parametrize("payload", ["[]", "{"])
def test_main_reports_invalid_present_config_without_traceback(
    tmp_path,
    monkeypatch,
    capsys,
    payload,
):
    config_path = tmp_path / "config.json"
    config_path.write_text(payload)
    monkeypatch.setattr(
        sys,
        "argv",
        ["epoch_reporter.py", "--config", str(config_path), "--once"],
    )

    with pytest.raises(SystemExit) as exc_info:
        epoch_reporter.main()

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "invalid config:" in stderr
    assert "Traceback" not in stderr
