import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reward_casebook import (  # noqa: E402
    CasebookError,
    MANIFEST_SCHEMA,
    compile_casebook,
    load_manifest,
    verify_casebook,
)
from reward_reconciliation import (  # noqa: E402
    SOURCE_SCHEMA,
    compile_reconciliation,
)


def row(ts, epoch, balance):
    return {"observed_at": ts, "epoch": epoch, "balance_rtc": balance}


def source(*rows, miner_id="miner-alpha"):
    return {
        "schema_version": SOURCE_SCHEMA,
        "miner_id": miner_id,
        "observations": list(rows),
    }


def evidence(source_value):
    return source_value, compile_reconciliation(source_value)


class RewardCasebookTests(unittest.TestCase):
    def caution_evidence(self, miner_id="miner-alpha"):
        return evidence(source(
            row("2026-09-13T09:00:00Z", 100, "10"),
            row("2026-09-13T09:10:00Z", 101, "10"),
            miner_id=miner_id,
        ))

    def anomaly_evidence(self, miner_id="miner-beta"):
        return evidence(source(
            row("2026-09-13T09:00:00Z", 200, "20"),
            row("2026-09-13T09:10:00Z", 201, "19.5"),
            miner_id=miner_id,
        ))

    def healthy_evidence(self):
        return evidence(source(
            row("2026-09-13T09:00:00Z", 300, "30"),
            row("2026-09-13T09:10:00Z", 301, "30.25"),
            miner_id="miner-healthy",
        ))

    def test_materializes_only_caution_and_anomaly(self):
        artifact = compile_casebook(
            [self.healthy_evidence(), self.caution_evidence(), self.anomaly_evidence()],
            as_of="2026-09-13T10:00:00Z",
        )
        cases = artifact["casebook"]["cases"]
        self.assertEqual(len(cases), 2)
        self.assertEqual([case["severity"] for case in cases], ["ANOMALY", "CAUTION"])
        self.assertEqual(artifact["casebook"]["summary"]["anomaly_count"], 1)
        self.assertEqual(artifact["casebook"]["summary"]["caution_count"], 1)
        self.assertNotIn("miner-healthy", {case["miner_id"] for case in cases})

    def test_deterministic_and_verifiable(self):
        pairs = [self.caution_evidence(), self.anomaly_evidence()]
        first = compile_casebook(pairs, as_of="2026-09-13T10:00:00Z")
        second = compile_casebook(pairs, as_of="2026-09-13T10:00:00Z")
        self.assertEqual(first, second)
        self.assertTrue(
            verify_casebook(pairs, first, as_of="2026-09-13T10:00:00Z")
        )

    def test_case_id_is_stable_across_disposition_and_as_of_changes(self):
        pair = self.caution_evidence()
        initial = compile_casebook([pair], as_of="2026-09-13T10:00:00Z")
        case_id = initial["casebook"]["cases"][0]["case_id"]
        disposition = [{
            "case_id": case_id,
            "status": "ACKNOWLEDGED",
            "updated_at": "2026-09-13T09:30:00Z",
            "note": "operator is checking the node snapshot",
        }]
        later = compile_casebook(
            [pair],
            dispositions=disposition,
            as_of="2026-09-13T11:00:00Z",
        )
        case = later["casebook"]["cases"][0]
        self.assertEqual(case["case_id"], case_id)
        self.assertEqual(case["status"], "ACKNOWLEDGED")
        self.assertEqual(case["operator_note"], disposition[0]["note"])
        self.assertGreater(case["age_seconds_floor"], initial["casebook"]["cases"][0]["age_seconds_floor"])

    def test_resolved_disposition_is_explicit_not_automatic(self):
        pair = self.anomaly_evidence()
        initial = compile_casebook([pair], as_of="2026-09-13T10:00:00Z")
        case_id = initial["casebook"]["cases"][0]["case_id"]
        resolved = compile_casebook(
            [pair],
            dispositions=[{
                "case_id": case_id,
                "status": "RESOLVED",
                "updated_at": "2026-09-13T09:45:00Z",
                "note": "matched against a trusted accounting correction",
            }],
            as_of="2026-09-13T10:00:00Z",
        )
        self.assertEqual(resolved["casebook"]["summary"]["resolved_count"], 1)
        self.assertEqual(resolved["casebook"]["summary"]["open_count"], 0)
        self.assertFalse(resolved["casebook"]["authority"]["automatic_resolution"])

    def test_unknown_disposition_fails_closed(self):
        with self.assertRaisesRegex(CasebookError, "unknown case ids"):
            compile_casebook(
                [self.caution_evidence()],
                dispositions=[{
                    "case_id": "rcase-" + "0" * 64,
                    "status": "RESOLVED",
                    "updated_at": "2026-09-13T09:30:00Z",
                    "note": "",
                }],
                as_of="2026-09-13T10:00:00Z",
            )

    def test_duplicate_disposition_fails_closed(self):
        pair = self.caution_evidence()
        case_id = compile_casebook(
            [pair], as_of="2026-09-13T10:00:00Z"
        )["casebook"]["cases"][0]["case_id"]
        item = {
            "case_id": case_id,
            "status": "ACKNOWLEDGED",
            "updated_at": "2026-09-13T09:30:00Z",
            "note": "",
        }
        with self.assertRaisesRegex(CasebookError, "duplicate disposition"):
            compile_casebook(
                [pair],
                dispositions=[item, dict(item)],
                as_of="2026-09-13T10:00:00Z",
            )

    def test_future_and_predating_dispositions_fail_closed(self):
        pair = self.caution_evidence()
        case_id = compile_casebook(
            [pair], as_of="2026-09-13T10:00:00Z"
        )["casebook"]["cases"][0]["case_id"]
        for updated_at, pattern in (
            ("2026-09-13T10:00:01Z", "after as_of"),
            ("2026-09-13T09:09:59Z", "predates"),
        ):
            with self.subTest(updated_at=updated_at):
                with self.assertRaisesRegex(CasebookError, pattern):
                    compile_casebook(
                        [pair],
                        dispositions=[{
                            "case_id": case_id,
                            "status": "ACKNOWLEDGED",
                            "updated_at": updated_at,
                            "note": "",
                        }],
                        as_of="2026-09-13T10:00:00Z",
                    )

    def test_as_of_before_case_observation_fails_closed(self):
        with self.assertRaisesRegex(CasebookError, "after as_of"):
            compile_casebook(
                [self.caution_evidence()],
                as_of="2026-09-13T09:09:59Z",
            )

    def test_duplicate_evidence_fails_closed(self):
        pair = self.caution_evidence()
        with self.assertRaisesRegex(CasebookError, "duplicate reconciliation evidence"):
            compile_casebook(
                [pair, pair],
                as_of="2026-09-13T10:00:00Z",
            )

    def test_tampered_reconciliation_fails_full_recompile(self):
        source_value, artifact = self.caution_evidence()
        tampered = copy.deepcopy(artifact)
        tampered["report"]["transitions"][0]["balance_delta_rtc"] = "999"
        with self.assertRaisesRegex(CasebookError, "failed full reconciliation verification"):
            compile_casebook(
                [(source_value, tampered)],
                as_of="2026-09-13T10:00:00Z",
            )

    def test_tampered_casebook_fails_verification(self):
        pairs = [self.caution_evidence()]
        artifact = compile_casebook(pairs, as_of="2026-09-13T10:00:00Z")
        tampered = copy.deepcopy(artifact)
        tampered["casebook"]["summary"]["open_count"] = 999
        self.assertFalse(
            verify_casebook(pairs, tampered, as_of="2026-09-13T10:00:00Z")
        )

    def test_authority_flags_are_all_false(self):
        artifact = compile_casebook(
            [self.caution_evidence()],
            as_of="2026-09-13T10:00:00Z",
        )
        authority = artifact["casebook"]["authority"]
        self.assertTrue(authority)
        self.assertTrue(all(value is False for value in authority.values()))
        self.assertFalse(authority["payout_owed"])
        self.assertFalse(authority["expected_reward_inferred"])
        self.assertFalse(authority["revenue_recognition"])

    def test_all_info_input_is_valid_empty_casebook(self):
        artifact = compile_casebook(
            [self.healthy_evidence()],
            as_of="2026-09-13T10:00:00Z",
        )
        self.assertEqual(artifact["casebook"]["cases"], [])
        self.assertEqual(artifact["casebook"]["summary"]["case_count"], 0)
        self.assertIn("| _none_", artifact["markdown"])

    def _write_manifest_fixture(self, directory):
        directory = Path(directory)
        source_value, reconciliation = self.caution_evidence()
        source_path = directory / "source.json"
        reconciliation_path = directory / "reconciliation.json"
        manifest_path = directory / "casebook-manifest.json"
        source_path.write_text(json.dumps(source_value), encoding="utf-8")
        reconciliation_path.write_text(json.dumps(reconciliation), encoding="utf-8")
        manifest_path.write_text(json.dumps({
            "schema_version": MANIFEST_SCHEMA,
            "as_of": "2026-09-13T10:00:00Z",
            "entries": [{
                "source": source_path.name,
                "artifact": reconciliation_path.name,
            }],
            "dispositions": [],
        }), encoding="utf-8")
        return manifest_path

    def test_manifest_loader_and_cli_compile_verify_no_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            manifest_path = self._write_manifest_fixture(td)
            pairs, dispositions, as_of = load_manifest(manifest_path)
            self.assertEqual(len(pairs), 1)
            self.assertEqual(dispositions, [])
            self.assertEqual(as_of, "2026-09-13T10:00:00Z")

            json_out = td / "casebook.json"
            md_out = td / "casebook.md"
            compile_run = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "reward_casebook.py"),
                    "compile",
                    str(manifest_path),
                    "--json-out",
                    str(json_out),
                    "--markdown-out",
                    str(md_out),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(compile_run.returncode, 0, compile_run.stderr)
            self.assertTrue(json_out.is_file())
            self.assertTrue(md_out.is_file())

            verify_run = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "reward_casebook.py"),
                    "verify",
                    str(manifest_path),
                    str(json_out),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(verify_run.returncode, 0, verify_run.stderr)
            self.assertEqual(json.loads(verify_run.stdout), {"ok": True})

            overwrite = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "reward_casebook.py"),
                    "compile",
                    str(manifest_path),
                    "--json-out",
                    str(json_out),
                    "--markdown-out",
                    str(td / "second.md"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(overwrite.returncode, 2)
            self.assertIn("refusing to overwrite", overwrite.stderr)

    def test_manifest_path_escape_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            outside = td.parent / "outside-casebook-source.json"
            outside.write_text("{}", encoding="utf-8")
            try:
                manifest = td / "manifest.json"
                manifest.write_text(json.dumps({
                    "schema_version": MANIFEST_SCHEMA,
                    "as_of": "2026-09-13T10:00:00Z",
                    "entries": [{
                        "source": "../outside-casebook-source.json",
                        "artifact": "missing.json",
                    }],
                    "dispositions": [],
                }), encoding="utf-8")
                with self.assertRaisesRegex(CasebookError, "stay within"):
                    load_manifest(manifest)
            finally:
                outside.unlink(missing_ok=True)

    def test_manifest_duplicate_entry_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            manifest_path = self._write_manifest_fixture(td)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["entries"].append(dict(manifest["entries"][0]))
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(CasebookError, "duplicate manifest entry"):
                load_manifest(manifest_path)


if __name__ == "__main__":
    unittest.main()
