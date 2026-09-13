import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rustchain_monitor


@pytest.mark.parametrize("bad_row", [None, "https://node.example", 42, ["nested"]])
def test_load_node_targets_rejects_non_object_rows_with_indexed_value_error(tmp_path, bad_row):
    config_path = tmp_path / "nodes.json"
    config_path.write_text(json.dumps({"nodes": [{"url": "https://ok.example"}, bad_row]}))

    with pytest.raises(ValueError, match=r"node target 2 must be an object"):
        rustchain_monitor.load_node_targets(config_path)


def test_normalize_node_target_directly_rejects_non_object_row():
    with pytest.raises(ValueError, match=r"node target 1 must be an object"):
        rustchain_monitor.normalize_node_target(None)


def test_valid_node_target_contract_is_unchanged(tmp_path):
    config_path = tmp_path / "nodes.json"
    config_path.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "name": "Primary Node",
                        "role": "Primary",
                        "url": "https://primary.example/",
                    }
                ]
            }
        )
    )

    assert rustchain_monitor.load_node_targets(config_path) == [
        {
            "node_id": "primary-node",
            "name": "Primary Node",
            "role": "Primary",
            "url": "https://primary.example",
        }
    ]


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("url", ["https://evil.example"]),
        ("name", {"display": "Node 2"}),
        ("node_name", False),
        ("role", True),
        ("node_role", ["Secondary"]),
        ("node_id", 2),
    ],
)
def test_load_node_targets_rejects_non_string_fields_with_indexed_value_error(
    tmp_path, field, bad_value
):
    row = {
        "url": "https://node2.example",
        "name": "Node 2",
        "role": "Secondary",
        "node_id": "node2",
    }
    row[field] = bad_value
    config_path = tmp_path / "nodes.json"
    config_path.write_text(
        json.dumps(
            {
                "nodes": [
                    {"url": "https://ok.example"},
                    row,
                ]
            }
        )
    )

    with pytest.raises(
        ValueError,
        match=rf"node target 2 field '{field}' must be a string",
    ):
        rustchain_monitor.load_node_targets(config_path)


def test_null_optional_node_fields_keep_default_behavior():
    assert rustchain_monitor.normalize_node_target(
        {
            "url": "https://node.example/",
            "name": None,
            "node_name": None,
            "role": None,
            "node_role": None,
            "node_id": None,
        },
        index=1,
    ) == {
        "node_id": "node-2",
        "name": "Node 2",
        "role": "",
        "url": "https://node.example",
    }
