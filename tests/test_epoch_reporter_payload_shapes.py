import sys
from pathlib import Path


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


def test_run_once_ignores_non_mapping_epoch_without_false_alert(monkeypatch):
    sent_messages = []
    monkeypatch.setattr(epoch_reporter, "fetch_health", lambda node_url: _healthy_payload())
    monkeypatch.setattr(epoch_reporter, "fetch_epoch", lambda node_url: ["not", "an", "epoch"])
    monkeypatch.setattr(epoch_reporter, "fetch_miners", lambda node_url: [])
    monkeypatch.setattr(
        epoch_reporter,
        "notify_channels",
        lambda message, **kwargs: sent_messages.append(message) or True,
    )

    state = epoch_reporter.run_once(
        "https://node.example",
        epoch_reporter.default_state(),
        reward_min=0.5,
        reward_max=2.0,
    )

    assert state["last_epoch"] is None
    assert state["last_reward_alert_epoch"] is None
    assert sent_messages == []


def test_run_once_does_not_age_miners_on_malformed_collection(monkeypatch):
    monkeypatch.setattr(epoch_reporter, "fetch_health", lambda node_url: _healthy_payload())
    monkeypatch.setattr(epoch_reporter, "fetch_epoch", lambda node_url: None)
    monkeypatch.setattr(epoch_reporter, "fetch_miners", lambda node_url: {"miner": "unexpected-object"})

    state = epoch_reporter.default_state()
    state["tracked_miners"] = {
        "miner-a": {
            "device_arch": "x86_64",
            "last_attest": None,
            "missed_polls": 1,
            "offline_alerted": False,
        }
    }

    updated = epoch_reporter.run_once(
        "https://node.example",
        state,
        offline_polls=2,
    )

    assert updated["tracked_miners"]["miner-a"]["missed_polls"] == 1
    assert updated["tracked_miners"]["miner-a"]["offline_alerted"] is False


def test_run_once_does_not_age_miners_on_malformed_identifier(monkeypatch):
    monkeypatch.setattr(epoch_reporter, "fetch_health", lambda node_url: _healthy_payload())
    monkeypatch.setattr(epoch_reporter, "fetch_epoch", lambda node_url: None)
    monkeypatch.setattr(epoch_reporter, "fetch_miners", lambda node_url: [{"miner": ["not-hashable"]}])

    state = epoch_reporter.default_state()
    state["tracked_miners"] = {
        "miner-a": {
            "device_arch": "x86_64",
            "last_attest": None,
            "missed_polls": 1,
            "offline_alerted": False,
        }
    }

    updated = epoch_reporter.run_once(
        "https://node.example",
        state,
        offline_polls=2,
    )

    assert updated["tracked_miners"]["miner-a"]["missed_polls"] == 1
    assert updated["tracked_miners"]["miner-a"]["offline_alerted"] is False


def test_mixed_miner_records_fail_closed_before_tracking_mutation():
    state = {}

    messages = epoch_reporter.update_miner_tracking(
        state,
        [{"miner": "valid-looking"}, "not-a-record"],
        offline_polls=2,
    )

    assert messages == []
    assert state == {}


def test_malformed_miner_identifier_fails_closed_before_tracking_mutation():
    for malformed_id in (["list-id"], {"nested": "id"}, 1, True):
        state = {
            "tracked_miners": {
                "miner-a": {
                    "device_arch": "x86_64",
                    "last_attest": None,
                    "missed_polls": 1,
                    "offline_alerted": False,
                }
            }
        }

        messages = epoch_reporter.update_miner_tracking(
            state,
            [{"miner": malformed_id}],
            offline_polls=2,
        )

        assert messages == []
        assert state["tracked_miners"]["miner-a"]["missed_polls"] == 1
        assert state["tracked_miners"]["miner-a"]["offline_alerted"] is False
        assert list(state["tracked_miners"]) == ["miner-a"]


def test_reward_helper_ignores_non_mapping_epoch_payloads():
    state = epoch_reporter.default_state()

    for payload in (["bad"], "bad", 1):
        message = epoch_reporter.check_reward_alert(
            state,
            payload,
            reward_min=0.5,
            reward_max=2.0,
        )
        assert message is None
        assert epoch_reporter.extract_reward_value(payload) is None

    assert state["last_reward_alert_epoch"] is None
