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
