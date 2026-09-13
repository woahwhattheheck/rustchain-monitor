import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rustchain_monitor import RustChainMonitor, _health_db_rw, build_aggregate_export_metrics


MALFORMED_TRUTHY = ["false", "true", 1, -1, ["false"], {"value": False}]


def _monitor_with_health(health):
    monitor = RustChainMonitor(node_url="https://node.example.test")
    monitor.get_health = Mock(return_value=health)
    monitor.get_epoch = Mock(return_value={"epoch": 8})
    monitor.get_miners = Mock(return_value=[])
    return monitor


@pytest.mark.parametrize("value", MALFORMED_TRUTHY)
def test_health_ok_requires_literal_json_boolean_true(value):
    snapshot = _monitor_with_health({"ok": value, "db_rw": True}).collect_network_snapshot()

    assert snapshot["summary"]["node_ok"] is False
    aggregate = build_aggregate_export_metrics([snapshot])
    healthy = next(metric for metric in aggregate if metric["name"] == "rustchain_nodes_healthy_total")
    assert healthy["value"] == 0


@pytest.mark.parametrize("value", MALFORMED_TRUTHY)
def test_db_rw_requires_literal_json_boolean_true(value):
    assert _health_db_rw({"db_rw": value}) is False


@pytest.mark.parametrize("value", [False, "false", 1, -1, [], {}])
def test_explicit_db_rw_never_falls_back_to_legacy_rw(value):
    assert _health_db_rw({"db_rw": value, "db": "rw"}) is False


def test_protocol_boolean_true_remains_healthy_and_rw():
    snapshot = _monitor_with_health({"ok": True, "db_rw": True}).collect_network_snapshot()

    assert snapshot["summary"]["node_ok"] is True
    assert snapshot["summary"]["db_rw"] is True


def test_protocol_boolean_false_remains_unhealthy_and_read_only():
    snapshot = _monitor_with_health({"ok": False, "db_rw": False}).collect_network_snapshot()

    assert snapshot["summary"]["node_ok"] is False
    assert snapshot["summary"]["db_rw"] is False


@pytest.mark.parametrize("value", ["rw", " RW ", "read-write", "READ_WRITE", "readwrite"])
def test_legacy_rw_spellings_are_preserved_when_db_rw_is_absent(value):
    assert _health_db_rw({"db": value}) is True


@pytest.mark.parametrize("value", ["read-only", "grow", "rw-primary", "read-write-enabled", "", None])
def test_legacy_db_requires_an_exact_rw_spelling(value):
    assert _health_db_rw({"db": value}) is False
