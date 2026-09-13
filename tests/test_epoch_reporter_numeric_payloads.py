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
