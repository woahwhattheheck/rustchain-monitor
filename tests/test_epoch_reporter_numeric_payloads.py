import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import epoch_reporter


def test_format_epoch_message_normalizes_numeric_strings():
    message = epoch_reporter.format_epoch_message(
        {"epoch": 124, "epoch_pot": "1.5", "enrolled_miners": "2"},
        [],
        "https://node.example",
    )

    assert "Reward pot: 1.5 RTC" in message
    assert "Enrolled miners: 2" in message
    assert "Estimated RTC distributed: 3.0" in message


def test_run_once_survives_numeric_string_epoch_fields(monkeypatch):
    sent_messages = []

    monkeypatch.setattr(
        epoch_reporter,
        "fetch_health",
        lambda node_url: {
            "ok": True,
            "db_rw": True,
            "tip_age_slots": 0,
            "backup_age_hours": 1.0,
            "version": "2.2.1-rip200",
        },
    )
    monkeypatch.setattr(
        epoch_reporter,
        "fetch_epoch",
        lambda node_url: {
            "epoch": 124,
            "epoch_pot": "1.5",
            "enrolled_miners": "2",
        },
    )
    monkeypatch.setattr(epoch_reporter, "fetch_miners", lambda node_url: [])
    monkeypatch.setattr(
        epoch_reporter,
        "notify_channels",
        lambda message, **kwargs: sent_messages.append(message) or True,
    )

    state = epoch_reporter.run_once(
        "https://node.example",
        epoch_reporter.default_state(),
    )

    assert state["last_epoch"] == 124
    assert state["last_posted"] is not None
    assert len(sent_messages) == 1
    assert "Estimated RTC distributed: 3.0" in sent_messages[0]


def test_float_parser_rejects_nonfinite_values():
    for value in ("NaN", "Infinity", "-Infinity", math.nan, math.inf, -math.inf):
        assert epoch_reporter._float_or_none(value) is None

    assert epoch_reporter._float_or_none("1.25") == 1.25


def test_health_payload_rejects_truthy_string_booleans():
    problems = epoch_reporter.health_problems(
        {
            "ok": "false",
            "db_rw": "false",
            "tip_age_slots": "0",
            "backup_age_hours": "1.0",
        },
        tip_age_max=100,
        backup_age_max_hours=6.0,
    )

    assert "node health check returned not-ok" in problems
    assert "database is not read-write" in problems


def test_health_payload_rejects_nonfinite_threshold_values():
    problems = epoch_reporter.health_problems(
        {
            "ok": True,
            "db_rw": True,
            "tip_age_slots": "NaN",
            "backup_age_hours": "Infinity",
        },
        tip_age_max=100,
        backup_age_max_hours=6.0,
    )

    assert "tip age is not a finite number" in problems
    assert "backup age is not a finite number" in problems


def test_health_payload_accepts_finite_numeric_strings():
    problems = epoch_reporter.health_problems(
        {
            "ok": True,
            "db_rw": True,
            "tip_age_slots": "4",
            "backup_age_hours": "1.5",
        },
        tip_age_max=100,
        backup_age_max_hours=6.0,
    )

    assert problems == []


def test_invalid_reward_is_alerted_when_thresholds_are_configured():
    state = epoch_reporter.default_state()
    message = epoch_reporter.check_reward_alert(
        state,
        {"epoch": 125, "reward": "NaN"},
        reward_min=0.5,
        reward_max=2.0,
    )

    assert message is not None
    assert "Observed reward is not a finite number" in message
    assert state["last_reward_alert_epoch"] == 125

    assert (
        epoch_reporter.check_reward_alert(
            state,
            {"epoch": 125, "reward": "Infinity"},
            reward_min=0.5,
            reward_max=2.0,
        )
        is None
    )
