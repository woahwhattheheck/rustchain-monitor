import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import reward_casebook as mod  # noqa: E402
from reward_casebook import CasebookError, MANIFEST_SCHEMA, load_manifest  # noqa: E402
from reward_reconciliation import (  # noqa: E402
    SOURCE_SCHEMA,
    compile_reconciliation,
)


def _fixture(root: Path) -> Path:
    source = {
        "schema_version": SOURCE_SCHEMA,
        "miner_id": "miner-file-custody",
        "observations": [
            {
                "observed_at": "2026-09-13T09:00:00Z",
                "epoch": 100,
                "balance_rtc": "10",
            },
            {
                "observed_at": "2026-09-13T09:10:00Z",
                "epoch": 101,
                "balance_rtc": "10",
            },
        ],
    }
    artifact = compile_reconciliation(source)
    (root / "source.json").write_text(json.dumps(source), encoding="utf-8")
    (root / "reconciliation.json").write_text(
        json.dumps(artifact), encoding="utf-8"
    )
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps({
            "schema_version": MANIFEST_SCHEMA,
            "as_of": "2026-09-13T10:00:00Z",
            "entries": [{
                "source": "source.json",
                "artifact": "reconciliation.json",
            }],
            "dispositions": [],
        }),
        encoding="utf-8",
    )
    return manifest


@pytest.mark.parametrize(
    ("read_number", "target_name"),
    [
        (1, "manifest.json"),
        (2, "source.json"),
        (3, "reconciliation.json"),
    ],
)
def test_regular_file_swapped_to_symlink_after_open_is_rejected(
    tmp_path,
    monkeypatch,
    read_number,
    target_name,
):
    manifest = _fixture(tmp_path)
    target = tmp_path / target_name
    decoy = tmp_path / f"decoy-{read_number}.json"
    decoy.write_text("{}", encoding="utf-8")
    real_read = mod._read_fd_bytes
    calls = 0

    def swap_after_open(fd, field):
        nonlocal calls
        calls += 1
        if calls == read_number:
            target.unlink()
            target.symlink_to(decoy.name)
        return real_read(fd, field)

    monkeypatch.setattr(mod, "_read_fd_bytes", swap_after_open)
    with pytest.raises(CasebookError, match="changed while it was being read"):
        load_manifest(manifest)


def test_direct_symlink_entry_is_rejected(tmp_path):
    manifest = _fixture(tmp_path)
    source = tmp_path / "source.json"
    regular = tmp_path / "source-regular.json"
    source.rename(regular)
    source.symlink_to(regular.name)

    with pytest.raises(CasebookError, match="symlink"):
        load_manifest(manifest)


def test_symlink_directory_component_is_rejected(tmp_path):
    manifest = _fixture(tmp_path)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "source.json").write_bytes((tmp_path / "source.json").read_bytes())
    linked_dir = tmp_path / "linked-evidence"
    linked_dir.symlink_to(evidence_dir.name, target_is_directory=True)

    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["entries"][0]["source"] = "linked-evidence/source.json"
    manifest.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(CasebookError, match="symlink"):
        load_manifest(manifest)


def test_hard_link_aliases_are_duplicate_evidence_entries(tmp_path):
    manifest = _fixture(tmp_path)
    os.link(tmp_path / "source.json", tmp_path / "source-alias.json")
    os.link(
        tmp_path / "reconciliation.json",
        tmp_path / "reconciliation-alias.json",
    )
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["entries"].append({
        "source": "source-alias.json",
        "artifact": "reconciliation-alias.json",
    })
    manifest.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(CasebookError, match="duplicate manifest entry"):
        load_manifest(manifest)
