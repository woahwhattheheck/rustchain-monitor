import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import epoch_reporter


def _healthy():
    return {
        "ok": True,
        "db_rw": True,
        "tip_age_slots": 0,
        "backup_age_hours": 1.0,
        "version": "test",
    }


def _unhealthy():
    return {
        "ok": True,
        "db_rw": False,
        "tip_age_slots": 250,
        "backup_age_hours": 1.0,
        "version": "test",
    }


def test_health_alert_retries_until_configured_delivery_succeeds(monkeypatch):
    outcomes = iter([False, True])
    attempts = []
    monkeypatch.setattr(epoch_reporter, "fetch_health", lambda _url: _unhealthy())
    monkeypatch.setattr(epoch_reporter, "fetch_epoch", lambda _url: None)
    monkeypatch.setattr(epoch_reporter, "fetch_miners", lambda _url: None)
    monkeypatch.setattr(
        epoch_reporter,
        "notify_channels",
        lambda message, **_kwargs: attempts.append(message) or next(outcomes),
    )

    state = epoch_reporter.run_once(
        "https://node.example",
        epoch_reporter.default_state(),
        slack_webhook="configured",
    )
    assert state["last_health_ok"] is None

    state = epoch_reporter.run_once(
        "https://node.example",
        state,
        slack_webhook="configured",
    )
    assert state["last_health_ok"] is False
    assert len(attempts) == 2
    assert all("Network health alert" in message for message in attempts)


def test_health_recovery_retries_until_configured_delivery_succeeds(monkeypatch):
    outcomes = iter([False, True])
    attempts = []
    monkeypatch.setattr(epoch_reporter, "fetch_health", lambda _url: _healthy())
    monkeypatch.setattr(epoch_reporter, "fetch_epoch", lambda _url: None)
    monkeypatch.setattr(epoch_reporter, "fetch_miners", lambda _url: None)
    monkeypatch.setattr(
        epoch_reporter,
        "notify_channels",
        lambda message, **_kwargs: attempts.append(message) or next(outcomes),
    )

    state = epoch_reporter.default_state()
    state["last_health_ok"] = False
    state = epoch_reporter.run_once(
        "https://node.example",
        state,
        slack_webhook="configured",
    )
    assert state["last_health_ok"] is False

    state = epoch_reporter.run_once(
        "https://node.example",
        state,
        slack_webhook="configured",
    )
    assert state["last_health_ok"] is True
    assert len(attempts) == 2
    assert all("Network health recovered" in message for message in attempts)


def test_miner_offline_alert_retries_until_configured_delivery_succeeds(monkeypatch):
    outcomes = iter([False, True])
    attempts = []
    miner = {"miner": "miner-a", "device_arch": "g4", "last_attest": 1000}
    miner_polls = iter([[miner], [], []])
    monkeypatch.setattr(epoch_reporter, "fetch_health", lambda _url: _healthy())
    monkeypatch.setattr(epoch_reporter, "fetch_epoch", lambda _url: None)
    monkeypatch.setattr(epoch_reporter, "fetch_miners", lambda _url: next(miner_polls))
    monkeypatch.setattr(
        epoch_reporter,
        "notify_channels",
        lambda message, **_kwargs: attempts.append(message) or next(outcomes),
    )

    state = epoch_reporter.run_once(
        "https://node.example",
        epoch_reporter.default_state(),
        slack_webhook="configured",
        offline_polls=1,
    )
    assert state["tracked_miners"]["miner-a"]["offline_alerted"] is False

    state = epoch_reporter.run_once(
        "https://node.example",
        state,
        slack_webhook="configured",
        offline_polls=1,
    )
    assert state["tracked_miners"]["miner-a"]["offline_alerted"] is False

    state = epoch_reporter.run_once(
        "https://node.example",
        state,
        slack_webhook="configured",
        offline_polls=1,
    )
    assert state["tracked_miners"]["miner-a"]["offline_alerted"] is True
    assert len(attempts) == 2
    assert all("Miner offline alert" in message for message in attempts)


def test_miner_recovery_retries_until_configured_delivery_succeeds(monkeypatch):
    outcomes = iter([False, True])
    attempts = []
    miner = {"miner": "miner-a", "device_arch": "g4", "last_attest": 1060}
    monkeypatch.setattr(epoch_reporter, "fetch_health", lambda _url: _healthy())
    monkeypatch.setattr(epoch_reporter, "fetch_epoch", lambda _url: None)
    monkeypatch.setattr(epoch_reporter, "fetch_miners", lambda _url: [miner])
    monkeypatch.setattr(
        epoch_reporter,
        "notify_channels",
        lambda message, **_kwargs: attempts.append(message) or next(outcomes),
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

    state = epoch_reporter.run_once(
        "https://node.example",
        state,
        slack_webhook="configured",
    )
    assert state["tracked_miners"]["miner-a"]["offline_alerted"] is True

    state = epoch_reporter.run_once(
        "https://node.example",
        state,
        slack_webhook="configured",
    )
    assert state["tracked_miners"]["miner-a"]["offline_alerted"] is False
    assert len(attempts) == 2
    assert all("Miner recovery alert" in message for message in attempts)
