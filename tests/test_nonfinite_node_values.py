import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rustchain_monitor import (
    RustChainMonitor,
    _coerce_float,
    _coerce_int,
    build_grafana_export,
    record_history_snapshot,
    render_prometheus_metrics,
)


def response(body):
    result = requests.Response()
    result.status_code = 200
    result.url = "https://node.example.test/endpoint"
    result._content = json.dumps(body).encode("utf-8")
    return result


NONFINITE_VALUES = [
    float("nan"),
    float("inf"),
    float("-inf"),
    "NaN",
    "Infinity",
    "-Infinity",
]


@pytest.mark.parametrize("value", NONFINITE_VALUES)
def test_float_coercion_rejects_nonfinite_values(value):
    assert _coerce_float(value, None) is None


def test_integer_coercion_rejects_infinite_float_without_raising():
    assert _coerce_int(float("inf"), None) is None
    assert _coerce_int(float("-inf"), None) is None


@pytest.mark.parametrize("value", NONFINITE_VALUES)
def test_miner_balance_rejects_nonfinite_node_values(value):
    monitor = RustChainMonitor(node_url="https://node.example.test")
    monitor.session.get = Mock(return_value=response({"balance_rtc": value}))

    with pytest.raises(ValueError, match="balance_rtc"):
        monitor.get_miner_balance("miner-a")


def test_zero_miner_balance_remains_valid():
    monitor = RustChainMonitor(node_url="https://node.example.test")
    monitor.session.get = Mock(return_value=response({"balance_rtc": 0.0}))

    assert monitor.get_miner_balance("miner-a") == 0.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_history_rejects_nonfinite_balance_before_creating_database(tmp_path, value):
    database = tmp_path / "history.db"

    with pytest.raises(ValueError, match="balance_rtc"):
        record_history_snapshot(
            database,
            miner_id="miner-a",
            epoch=1,
            balance_rtc=value,
            observed_at=1_700_000_000.0,
        )

    assert not database.exists()


def test_snapshot_and_exports_do_not_publish_nonfinite_node_metrics():
    monitor = RustChainMonitor(node_url="https://node.example.test")
    monitor.get_health = Mock(
        return_value={
            "ok": True,
            "version": "test",
            "uptime_s": float("nan"),
            "backup_age_hours": float("inf"),
            "tip_age_slots": float("-inf"),
        }
    )
    monitor.get_epoch = Mock(return_value={"epoch": float("inf")})
    monitor.get_miners = Mock(return_value=[])

    snapshot = monitor.collect_network_snapshot()

    assert snapshot["summary"]["epoch_current"] is None
    assert snapshot["summary"]["uptime_seconds"] is None
    assert snapshot["summary"]["backup_age_hours"] is None
    assert snapshot["summary"]["tip_age_slots"] is None

    prometheus = render_prometheus_metrics(snapshot)
    assert " nan" not in prometheus.lower()
    assert " inf" not in prometheus.lower()

    grafana = build_grafana_export(snapshot)
    # Strict JSON encoding is the final proof that no NaN/Infinity survives the
    # Grafana export surface, including the embedded raw snapshot.
    json.dumps(grafana, allow_nan=False)
