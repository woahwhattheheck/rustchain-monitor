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


def test_normalize_state_filters_invalid_pending_delivery_receipts():
    state = epoch_reporter.normalize_state(
        {
            "pending_delivery_targets": {
                'health:["alert"]': [
                    "discord",
                    "discord",
                    "slack",
                    "unknown",
                    17,
                ],
                5: ["discord"],
                "bad-shape": "discord",
                "empty-after-filter": ["unknown", None],
            }
        }
    )

    assert state["pending_delivery_targets"] == {
        'health:["alert"]': ["discord", "slack"]
    }


def test_notify_channels_skips_targets_already_delivered(monkeypatch):
    attempts = []
    monkeypatch.setattr(
        epoch_reporter,
        "post_to_discord",
        lambda _url, _message: attempts.append("discord") or True,
    )
    monkeypatch.setattr(
        epoch_reporter,
        "post_to_slack",
        lambda _url, _message: attempts.append("slack") or True,
    )

    delivered_targets = {"discord"}
    complete = epoch_reporter.notify_channels(
        "alert",
        discord_webhook="discord",
        slack_webhook="slack",
        delivered_targets=delivered_targets,
    )

    assert complete is True
    assert attempts == ["slack"]
    assert delivered_targets == {"discord", "slack"}


def test_health_retry_persists_success_and_retries_only_failed_route(
    monkeypatch, tmp_path
):
    attempts = []
    slack_outcomes = iter([False, True])
    monkeypatch.setattr(epoch_reporter, "fetch_health", lambda _url: _unhealthy())
    monkeypatch.setattr(epoch_reporter, "fetch_epoch", lambda _url: None)
    monkeypatch.setattr(epoch_reporter, "fetch_miners", lambda _url: None)
    monkeypatch.setattr(
        epoch_reporter,
        "post_to_discord",
        lambda _url, _message: attempts.append("discord") or True,
    )
    monkeypatch.setattr(
        epoch_reporter,
        "post_to_slack",
        lambda _url, _message: attempts.append("slack") or next(slack_outcomes),
    )

    state = epoch_reporter.run_once(
        "https://node.example",
        epoch_reporter.default_state(),
        discord_webhook="discord",
        slack_webhook="slack",
    )
    assert state["last_health_ok"] is None
    assert attempts == ["discord", "slack"]
    assert list(state["pending_delivery_targets"].values()) == [["discord"]]

    state_file = tmp_path / "state.json"
    epoch_reporter.save_state(str(state_file), state)
    state = epoch_reporter.load_state(str(state_file))

    state = epoch_reporter.run_once(
        "https://node.example",
        state,
        discord_webhook="discord",
        slack_webhook="slack",
    )

    assert state["last_health_ok"] is False
    assert attempts == ["discord", "slack", "slack"]
    assert state["pending_delivery_targets"] == {}


def test_epoch_retry_does_not_duplicate_chat_after_moltbook_failure(monkeypatch):
    chat_attempts = []
    moltbook_attempts = []
    moltbook_outcomes = iter([False, True])
    monkeypatch.setattr(epoch_reporter, "fetch_health", lambda _url: _healthy())
    monkeypatch.setattr(
        epoch_reporter,
        "fetch_epoch",
        lambda _url: {"epoch": 42, "reward": 1.5, "height": 100},
    )
    monkeypatch.setattr(epoch_reporter, "fetch_miners", lambda _url: [])
    monkeypatch.setattr(
        epoch_reporter,
        "post_to_slack",
        lambda _url, _message: chat_attempts.append("slack") or True,
    )
    monkeypatch.setattr(
        epoch_reporter,
        "post_to_moltbook",
        lambda _key, _url, _message: (
            moltbook_attempts.append("moltbook") or next(moltbook_outcomes)
        ),
    )

    state = epoch_reporter.run_once(
        "https://node.example",
        epoch_reporter.default_state(),
        slack_webhook="slack",
        moltbook_key="key",
    )
    assert state["last_epoch"] is None
    assert chat_attempts == ["slack"]
    assert moltbook_attempts == ["moltbook"]
    assert list(state["pending_delivery_targets"].values()) == [["slack"]]

    state = epoch_reporter.run_once(
        "https://node.example",
        state,
        slack_webhook="slack",
        moltbook_key="key",
    )

    assert state["last_epoch"] == 42
    assert state["last_posted"] is not None
    assert chat_attempts == ["slack"]
    assert moltbook_attempts == ["moltbook", "moltbook"]
    assert state["pending_delivery_targets"] == {}
