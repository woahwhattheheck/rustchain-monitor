import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import epoch_reporter


def _healthy_payload():
    return {
        "ok": True,
        "db_rw": True,
        "tip_age_slots": 0,
        "backup_age_hours": 1.0,
        "version": "test",
    }


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("device_family", []),
        ("device_family", {}),
        ("hardware_type", []),
        ("hardware_type", 1),
        ("device_arch", {}),
        ("device_arch", True),
    ],
)
def test_run_once_rejects_structured_hardware_before_tracking_mutation(
    monkeypatch, field, bad_value
):
    monkeypatch.setattr(epoch_reporter, "fetch_health", lambda node_url: _healthy_payload())
    monkeypatch.setattr(epoch_reporter, "fetch_epoch", lambda node_url: {"epoch": 77})
    monkeypatch.setattr(
        epoch_reporter,
        "fetch_miners",
        lambda node_url: [{"miner": "miner-new", field: bad_value}],
    )
    monkeypatch.setattr(epoch_reporter, "notify_channels", lambda message, **kwargs: True)

    state = epoch_reporter.default_state()
    state["tracked_miners"] = {
        "miner-existing": {
            "device_arch": "x86_64",
            "last_attest": None,
            "missed_polls": 1,
            "offline_alerted": False,
        }
    }
    expected = deepcopy(state["tracked_miners"])

    updated = epoch_reporter.run_once(
        "https://node.example",
        state,
        offline_polls=2,
    )

    assert updated["tracked_miners"] == expected
    assert updated["last_epoch"] == 77


def test_string_hardware_labels_remain_trackable_and_formattable():
    miners = [
        {
            "miner": "miner-a",
            "device_family": "desktop",
            "hardware_type": "cpu",
            "device_arch": "x86_64",
            "last_attest": 1000,
        }
    ]
    state = epoch_reporter.default_state()

    messages = epoch_reporter.update_miner_tracking(state, miners, offline_polls=2)
    summary = epoch_reporter.format_epoch_message({"epoch": 77}, miners, "https://node.example")

    assert messages == []
    assert state["tracked_miners"]["miner-a"]["missed_polls"] == 0
    assert "Hardware mix: desktop: 1" in summary
