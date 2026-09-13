"""Persisted reporter-state shape regressions."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import epoch_reporter


def test_normalize_state_drops_non_mapping_tracked_rows():
    state = epoch_reporter.normalize_state(
        {"tracked_miners": {"broken": "not-an-object"}}
    )

    assert state["tracked_miners"] == {}


def test_invalid_missed_poll_counter_resets_before_tracking():
    state = epoch_reporter.normalize_state(
        {
            "tracked_miners": {
                "miner-a": {
                    "device_arch": "g4",
                    "missed_polls": "not-a-number",
                    "offline_alerted": False,
                }
            }
        }
    )

    assert state["tracked_miners"]["miner-a"]["missed_polls"] == 0
    assert epoch_reporter.update_miner_tracking(state, [], offline_polls=2) == []
    assert state["tracked_miners"]["miner-a"]["missed_polls"] == 1


def test_int_coercible_counter_and_valid_fields_are_preserved():
    state = epoch_reporter.normalize_state(
        {
            "tracked_miners": {
                "miner-a": {
                    "device_arch": "g5",
                    "last_attest": 123.0,
                    "missed_polls": "2",
                    "offline_alerted": True,
                    "extra": "preserve-me",
                }
            }
        }
    )

    assert state["tracked_miners"]["miner-a"] == {
        "device_arch": "g5",
        "last_attest": 123.0,
        "missed_polls": 2,
        "offline_alerted": True,
        "extra": "preserve-me",
    }


def test_active_miner_after_malformed_saved_row_is_tracked_without_bogus_recovery():
    state = epoch_reporter.normalize_state(
        {"tracked_miners": {"miner-a": ["broken"]}}
    )

    messages = epoch_reporter.update_miner_tracking(
        state,
        [{"miner": "miner-a", "device_arch": "g4", "last_attest": 456.0}],
        offline_polls=2,
    )

    assert messages == []
    assert state["tracked_miners"]["miner-a"] == {
        "device_arch": "g4",
        "last_attest": 456.0,
        "missed_polls": 0,
        "offline_alerted": False,
    }
