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
    ("field", "bad_id"),
    [
        ("miner", []),
        ("miner", {}),
        ("miner", 1),
        ("miner", True),
        ("miner_id", []),
        ("miner_id", {}),
        ("miner_id", 1),
        ("miner_id", True),
    ],
)
def test_run_once_rejects_non_string_miner_ids_before_tracking_mutation(
    monkeypatch, field, bad_id
):
    monkeypatch.setattr(epoch_reporter, "fetch_health", lambda node_url: _healthy_payload())
    monkeypatch.setattr(epoch_reporter, "fetch_epoch", lambda node_url: None)
    monkeypatch.setattr(epoch_reporter, "fetch_miners", lambda node_url: [{field: bad_id}])

    state = epoch_reporter.default_state()
    state["tracked_miners"] = {
        "miner-a": {
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


@pytest.mark.parametrize(
    "miners",
    [
        [{"miner": "miner-a"}, {"miner": "miner-a"}],
        [{"miner": "miner-a"}, {"miner_id": "miner-a"}],
        [{"miner": "miner-a", "miner_id": "miner-b"}],
    ],
)
def test_ambiguous_miner_identities_fail_closed_before_tracking_mutation(miners):
    state = epoch_reporter.default_state()
    state["tracked_miners"] = {
        "miner-a": {
            "device_arch": "g4",
            "last_attest": 1000,
            "missed_polls": 2,
            "offline_alerted": True,
        }
    }
    expected = deepcopy(state)

    messages = epoch_reporter.update_miner_tracking(
        state,
        miners,
        offline_polls=2,
    )

    assert messages == []
    assert state == expected


def test_duplicate_cross_alias_identity_does_not_emit_duplicate_recovery(
    monkeypatch,
):
    miners = [
        {"miner": "miner-a", "device_arch": "g4", "last_attest": 1060},
        {"miner_id": "miner-a", "device_arch": "g4", "last_attest": 1060},
    ]
    sent_messages = []
    monkeypatch.setattr(epoch_reporter, "fetch_health", lambda node_url: _healthy_payload())
    monkeypatch.setattr(epoch_reporter, "fetch_epoch", lambda node_url: None)
    monkeypatch.setattr(epoch_reporter, "fetch_miners", lambda node_url: miners)
    monkeypatch.setattr(
        epoch_reporter,
        "notify_channels",
        lambda message, **kwargs: sent_messages.append(message) or True,
    )

    state = epoch_reporter.default_state()
    state["last_health_ok"] = True
    state["tracked_miners"] = {
        "miner-a": {
            "device_arch": "g4",
            "last_attest": 1000,
            "missed_polls": 2,
            "offline_alerted": True,
        }
    }
    expected = deepcopy(state["tracked_miners"])

    updated = epoch_reporter.run_once(
        "https://node.example",
        state,
        slack_webhook="configured",
    )

    assert sent_messages == []
    assert updated["tracked_miners"] == expected


def test_matching_aliases_and_distinct_identities_remain_trackable():
    state = epoch_reporter.default_state()

    messages = epoch_reporter.update_miner_tracking(
        state,
        [
            {
                "miner": "miner-a",
                "miner_id": "miner-a",
                "device_arch": "g4",
                "last_attest": 1000,
            },
            {
                "miner_id": "miner-b",
                "device_arch": "x86_64",
                "last_attest": 1001,
            },
        ],
        offline_polls=2,
    )

    assert messages == []
    assert set(state["tracked_miners"]) == {"miner-a", "miner-b"}
    assert state["tracked_miners"]["miner-a"]["missed_polls"] == 0
    assert state["tracked_miners"]["miner-b"]["missed_polls"] == 0


def test_string_miner_identity_remains_trackable():
    state = epoch_reporter.default_state()

    messages = epoch_reporter.update_miner_tracking(
        state,
        [{"miner": "miner-a", "device_arch": "g4", "last_attest": 1000}],
        offline_polls=2,
    )

    assert messages == []
    assert state["tracked_miners"]["miner-a"]["missed_polls"] == 0
