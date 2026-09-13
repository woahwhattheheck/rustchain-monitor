import argparse
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


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


def test_health_payload_rejects_non_mapping_shapes_without_crashing():
    for payload in (["not", "a", "mapping"], "not-a-mapping", 1):
        state = epoch_reporter.default_state()
        messages = epoch_reporter.update_health_state(
            state,
            payload,
            node_url="https://node.example",
            tip_age_max=100,
            backup_age_max_hours=6.0,
        )

        assert state["last_health_ok"] is False
        assert len(messages) == 1
        assert "health endpoint returned invalid payload" in messages[0]


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


def _numeric_args(**overrides):
    values = {
        "interval": None,
        "offline_polls": None,
        "reward_min": None,
        "reward_max": None,
        "tip_age_max": None,
        "backup_age_max_hours": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _resolve_numeric(config=None, environ=None, **arg_overrides):
    parser = argparse.ArgumentParser(prog="epoch-reporter-test")
    return epoch_reporter.resolve_numeric_settings(
        parser,
        _numeric_args(**arg_overrides),
        config or {},
        {} if environ is None else environ,
    )


def test_numeric_settings_preserve_cli_env_config_default_precedence():
    config = {"poll_interval": 30, "offline_polls": 4}
    environ = {"POLL_INTERVAL": "20", "OFFLINE_POLLS": "3"}

    cli = _resolve_numeric(config, environ, interval=10, offline_polls=2)
    assert cli["poll_interval"] == 10
    assert cli["offline_polls"] == 2

    env = _resolve_numeric(config, environ)
    assert env["poll_interval"] == 20
    assert env["offline_polls"] == 3

    config_only = _resolve_numeric(config, {})
    assert config_only["poll_interval"] == 30
    assert config_only["offline_polls"] == 4

    defaults = _resolve_numeric({}, {})
    assert defaults["poll_interval"] == epoch_reporter.DEFAULT_INTERVAL
    assert defaults["offline_polls"] == epoch_reporter.DEFAULT_OFFLINE_POLLS


def test_empty_numeric_env_value_remains_unset():
    settings = _resolve_numeric(
        {"poll_interval": 30},
        {"POLL_INTERVAL": ""},
    )
    assert settings["poll_interval"] == 30


@pytest.mark.parametrize(
    ("config_key", "value"),
    [
        ("poll_interval", 0.5),
        ("poll_interval", 0),
        ("poll_interval", -1),
        ("poll_interval", "Infinity"),
        ("poll_interval", True),
        ("offline_polls", 0),
        ("offline_polls", -1),
        ("offline_polls", 1.5),
        ("offline_polls", "NaN"),
        ("tip_age_max", -1),
        ("tip_age_max", 1.5),
        ("backup_age_max_hours", -0.1),
        ("backup_age_max_hours", "Infinity"),
    ],
)
def test_invalid_resolved_numeric_config_fails_closed(config_key, value):
    with pytest.raises(SystemExit) as exc_info:
        _resolve_numeric({config_key: value}, {})

    assert exc_info.value.code == 2


def test_invalid_higher_priority_env_does_not_fall_back_to_config():
    with pytest.raises(SystemExit) as exc_info:
        _resolve_numeric(
            {"poll_interval": 30},
            {"POLL_INTERVAL": "0.5"},
        )

    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    ("config", "environ"),
    [
        ({"reward_min": "NaN"}, {}),
        ({"reward_max": "Infinity"}, {}),
        ({"reward_min": 3, "reward_max": 2}, {}),
        ({"reward_min": 1}, {"REWARD_MAX": "-Infinity"}),
    ],
)
def test_invalid_reward_thresholds_fail_closed(config, environ):
    with pytest.raises(SystemExit) as exc_info:
        _resolve_numeric(config, environ)

    assert exc_info.value.code == 2


def test_valid_numeric_config_is_normalized_without_truncation():
    settings = _resolve_numeric(
        {
            "poll_interval": "15.0",
            "offline_polls": 3.0,
            "reward_min": "0.25",
            "reward_max": 2,
            "tip_age_max": "0.0",
            "backup_age_max_hours": "1.5",
        },
        {},
    )

    assert settings == {
        "poll_interval": 15,
        "offline_polls": 3,
        "reward_min": 0.25,
        "reward_max": 2.0,
        "tip_age_max": 0,
        "backup_age_max_hours": 1.5,
    }
