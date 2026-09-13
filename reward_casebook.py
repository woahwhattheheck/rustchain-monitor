#!/usr/bin/env python3
"""Compile verified RustChain reward reconciliations into an operator exception casebook.

This module is evidence-only. It verifies every reconciliation by fully recompiling
it from the original source observations, then promotes only CAUTION / ANOMALY
transitions into stable operator cases. It never infers payout entitlement,
expected reward, customer acceptance, or revenue.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from reward_reconciliation import (
    ReconciliationError,
    load_json_strict,
    verify_reconciliation,
)

MANIFEST_SCHEMA = "rustchain.reward-casebook-manifest/v1"
CASEBOOK_SCHEMA = "rustchain.reward-casebook/v1"
RECEIPT_SCHEMA = "rustchain.reward-casebook-receipt/v1"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
CASE_ID_RE = re.compile(r"^rcase-[0-9a-f]{64}$")
MATERIAL_SEVERITIES = frozenset({"CAUTION", "ANOMALY"})
DISPOSITION_STATUSES = frozenset({"ACKNOWLEDGED", "RESOLVED"})
MANIFEST_KEYS = {"schema_version", "as_of", "entries", "dispositions"}
ENTRY_KEYS = {"source", "artifact"}
DISPOSITION_KEYS = {"case_id", "status", "updated_at", "note"}
AUTHORITY = {
    "payout_owed": False,
    "expected_reward_inferred": False,
    "node_mutation": False,
    "wallet_mutation": False,
    "payout_mutation": False,
    "provider_contact": False,
    "external_submission": False,
    "payment_acceptance": False,
    "revenue_recognition": False,
    "automatic_resolution": False,
}


class CasebookError(ValueError):
    """Malformed, ambiguous, or unverified casebook input."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _utc(value: Any, field: str) -> tuple[str, datetime]:
    if type(value) is not str or not UTC_RE.fullmatch(value):
        raise CasebookError(
            f"{field} must be UTC in YYYY-MM-DDTHH:MM:SS[.ffffff]Z form"
        )
    try:
        dt = datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise CasebookError(f"{field} is not a valid UTC timestamp") from exc
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    if dt.microsecond:
        base += "." + f"{dt.microsecond:06d}".rstrip("0")
    return base + "Z", dt


def _relative_path(value: Any, field: str) -> Path:
    if type(value) is not str or not value or "\x00" in value:
        raise CasebookError(f"{field} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise CasebookError(f"{field} must stay within the manifest directory")
    return path


def _safe_input_path(base: Path, value: Any, field: str) -> Path:
    relative = _relative_path(value, field)
    root = base.resolve()
    unresolved = base / relative
    if unresolved.is_symlink():
        raise CasebookError(f"{field} must not be a symlink")
    candidate = unresolved.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise CasebookError(f"{field} resolves outside the manifest directory") from exc
    if not candidate.is_file():
        raise CasebookError(f"{field} must resolve to an existing regular file")
    return candidate


def normalize_dispositions(value: Any, as_of_dt: datetime) -> dict[str, dict[str, str]]:
    if type(value) is not list:
        raise CasebookError("dispositions must be a list")
    out: dict[str, dict[str, str]] = {}
    for index, item in enumerate(value):
        field = f"dispositions[{index}]"
        if type(item) is not dict or set(item) != DISPOSITION_KEYS:
            keys = set(item) if type(item) is dict else set()
            raise CasebookError(
                f"{field} keys mismatch; "
                f"missing={sorted(DISPOSITION_KEYS - keys)}, "
                f"unknown={sorted(keys - DISPOSITION_KEYS)}"
            )
        case_id = item["case_id"]
        if type(case_id) is not str or CASE_ID_RE.fullmatch(case_id) is None:
            raise CasebookError(f"{field}.case_id is malformed")
        if case_id in out:
            raise CasebookError(f"duplicate disposition for {case_id}")
        status = item["status"]
        if status not in DISPOSITION_STATUSES:
            raise CasebookError(
                f"{field}.status must be one of {sorted(DISPOSITION_STATUSES)}"
            )
        updated_at, updated_dt = _utc(item["updated_at"], f"{field}.updated_at")
        if updated_dt > as_of_dt:
            raise CasebookError(f"{field}.updated_at is after as_of")
        note = item["note"]
        if type(note) is not str or len(note) > 2000:
            raise CasebookError(f"{field}.note must be a string no longer than 2000 chars")
        out[case_id] = {
            "status": status,
            "updated_at": updated_at,
            "note": note,
        }
    return out


def _case_identity(
    miner_id: str,
    receipt: Mapping[str, Any],
    transition: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "miner_id": miner_id,
        "source_sha256": receipt["source_sha256"],
        "report_sha256": receipt["report_sha256"],
        "from_observed_at": transition["from_observed_at"],
        "to_observed_at": transition["to_observed_at"],
        "from_epoch": transition["from_epoch"],
        "to_epoch": transition["to_epoch"],
        "severity": transition["severity"],
        "signals": transition["signals"],
    }


def _case_id(identity: Mapping[str, Any]) -> str:
    return "rcase-" + _sha(identity)


def _age_seconds_floor(as_of_dt: datetime, observed_at: str, field: str) -> int:
    _, observed_dt = _utc(observed_at, field)
    if observed_dt > as_of_dt:
        raise CasebookError(f"{field} is after as_of")
    delta = as_of_dt - observed_dt
    return delta.days * 86400 + delta.seconds


def compile_casebook(
    evidence_pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    *,
    dispositions: Sequence[Mapping[str, Any]] | None = None,
    as_of: str,
) -> dict[str, Any]:
    as_of, as_of_dt = _utc(as_of, "as_of")
    normalized_dispositions = normalize_dispositions(
        [] if dispositions is None else list(dispositions),
        as_of_dt,
    )
    if not evidence_pairs:
        raise CasebookError("at least one evidence pair is required")

    evidence_identities: list[dict[str, str]] = []
    cases: list[dict[str, Any]] = []
    seen_evidence: set[tuple[str, str, str]] = set()
    seen_case_ids: set[str] = set()

    for index, pair in enumerate(evidence_pairs):
        if (
            type(pair) not in (tuple, list)
            or len(pair) != 2
            or not isinstance(pair[0], Mapping)
            or not isinstance(pair[1], Mapping)
        ):
            raise CasebookError(f"evidence_pairs[{index}] must be (source, artifact)")
        source, artifact = pair
        try:
            verified = verify_reconciliation(source, artifact)
        except (ReconciliationError, TypeError, ValueError) as exc:
            raise CasebookError(
                f"evidence_pairs[{index}] could not be verified: {exc}"
            ) from exc
        if not verified:
            raise CasebookError(
                f"evidence_pairs[{index}] failed full reconciliation verification"
            )

        report = artifact["report"]
        _, last_observed_dt = _utc(
            report["last_observed_at"],
            f"evidence_pairs[{index}].report.last_observed_at",
        )
        if last_observed_dt > as_of_dt:
            raise CasebookError(
                f"evidence_pairs[{index}] contains observations after as_of"
            )
        receipt = artifact["receipt"]
        evidence_key = (
            receipt["source_sha256"],
            receipt["report_sha256"],
            receipt["markdown_sha256"],
        )
        if evidence_key in seen_evidence:
            raise CasebookError(
                f"duplicate reconciliation evidence at evidence_pairs[{index}]"
            )
        seen_evidence.add(evidence_key)
        evidence_identities.append(
            {
                "miner_id": report["miner_id"],
                "source_sha256": receipt["source_sha256"],
                "report_sha256": receipt["report_sha256"],
                "markdown_sha256": receipt["markdown_sha256"],
            }
        )

        for transition_index, transition in enumerate(report["transitions"]):
            severity = transition.get("severity")
            if severity not in {"INFO", *MATERIAL_SEVERITIES}:
                raise CasebookError(
                    f"evidence_pairs[{index}].transitions[{transition_index}] "
                    f"has unsupported severity {severity!r}"
                )
            if severity == "INFO":
                continue

            identity = _case_identity(report["miner_id"], receipt, transition)
            case_id = _case_id(identity)
            if case_id in seen_case_ids:
                raise CasebookError(f"duplicate material case identity: {case_id}")
            seen_case_ids.add(case_id)
            disposition = normalized_dispositions.get(case_id)
            status = disposition["status"] if disposition else "OPEN"
            updated_at = disposition["updated_at"] if disposition else None
            note = disposition["note"] if disposition else ""
            if updated_at is not None:
                _, updated_dt = _utc(updated_at, f"disposition[{case_id}].updated_at")
                _, observed_dt = _utc(
                    transition["to_observed_at"],
                    f"case[{case_id}].to_observed_at",
                )
                if updated_dt < observed_dt:
                    raise CasebookError(
                        f"disposition for {case_id} predates the case observation"
                    )

            cases.append(
                {
                    "case_id": case_id,
                    "miner_id": report["miner_id"],
                    "status": status,
                    "severity": severity,
                    "signals": list(transition["signals"]),
                    "from_observed_at": transition["from_observed_at"],
                    "to_observed_at": transition["to_observed_at"],
                    "age_seconds_floor": _age_seconds_floor(
                        as_of_dt,
                        transition["to_observed_at"],
                        f"case[{case_id}].to_observed_at",
                    ),
                    "from_epoch": transition["from_epoch"],
                    "to_epoch": transition["to_epoch"],
                    "epoch_delta": transition["epoch_delta"],
                    "from_balance_rtc": transition["from_balance_rtc"],
                    "to_balance_rtc": transition["to_balance_rtc"],
                    "balance_delta_rtc": transition["balance_delta_rtc"],
                    "source_sha256": receipt["source_sha256"],
                    "report_sha256": receipt["report_sha256"],
                    "markdown_sha256": receipt["markdown_sha256"],
                    "disposition_updated_at": updated_at,
                    "operator_note": note,
                }
            )

    unknown_dispositions = set(normalized_dispositions) - seen_case_ids
    if unknown_dispositions:
        raise CasebookError(
            "dispositions reference unknown case ids: "
            + ", ".join(sorted(unknown_dispositions))
        )

    severity_rank = {"ANOMALY": 0, "CAUTION": 1}
    cases.sort(
        key=lambda item: (
            severity_rank[item["severity"]],
            item["status"] == "RESOLVED",
            item["to_observed_at"],
            item["miner_id"],
            item["case_id"],
        )
    )
    evidence_identities.sort(
        key=lambda item: (
            item["miner_id"],
            item["source_sha256"],
            item["report_sha256"],
        )
    )
    summary = {
        "case_count": len(cases),
        "open_count": sum(item["status"] == "OPEN" for item in cases),
        "acknowledged_count": sum(
            item["status"] == "ACKNOWLEDGED" for item in cases
        ),
        "resolved_count": sum(item["status"] == "RESOLVED" for item in cases),
        "anomaly_count": sum(item["severity"] == "ANOMALY" for item in cases),
        "caution_count": sum(item["severity"] == "CAUTION" for item in cases),
    }
    input_identity = {
        "as_of": as_of,
        "evidence": evidence_identities,
        "dispositions": [
            {"case_id": case_id, **normalized_dispositions[case_id]}
            for case_id in sorted(normalized_dispositions)
        ],
    }
    casebook = {
        "schema_version": CASEBOOK_SCHEMA,
        "as_of": as_of,
        "input_evidence_count": len(evidence_identities),
        "input_identity_sha256": _sha(input_identity),
        "summary": summary,
        "cases": cases,
        "authority": dict(AUTHORITY),
    }
    markdown = _markdown(casebook)
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "casebook_sha256": _sha(casebook),
        "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "input_identity_sha256": casebook["input_identity_sha256"],
    }
    return {"casebook": casebook, "markdown": markdown, "receipt": receipt}


def verify_casebook(
    evidence_pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    artifact: Mapping[str, Any],
    *,
    dispositions: Sequence[Mapping[str, Any]] | None = None,
    as_of: str,
) -> bool:
    if type(artifact) is not dict or set(artifact) != {
        "casebook",
        "markdown",
        "receipt",
    }:
        return False
    try:
        expected = compile_casebook(
            evidence_pairs,
            dispositions=dispositions,
            as_of=as_of,
        )
    except CasebookError:
        return False
    if artifact != expected:
        return False
    receipt = artifact.get("receipt")
    if type(receipt) is not dict or set(receipt) != {
        "schema_version",
        "casebook_sha256",
        "markdown_sha256",
        "input_identity_sha256",
    }:
        return False
    return (
        receipt.get("schema_version") == RECEIPT_SCHEMA
        and all(
            type(receipt.get(key)) is str
            and HEX64_RE.fullmatch(receipt[key]) is not None
            for key in (
                "casebook_sha256",
                "markdown_sha256",
                "input_identity_sha256",
            )
        )
    )


def _markdown(casebook: Mapping[str, Any]) -> str:
    summary = casebook["summary"]
    lines = [
        "# RustChain reward exception casebook",
        "",
        f"- As of: `{casebook['as_of']}`",
        f"- Verified evidence sets: {casebook['input_evidence_count']}",
        f"- Material cases: {summary['case_count']}",
        f"- Open / acknowledged / resolved: "
        f"{summary['open_count']} / {summary['acknowledged_count']} / "
        f"{summary['resolved_count']}",
        f"- Anomaly / caution: {summary['anomaly_count']} / "
        f"{summary['caution_count']}",
        f"- Input identity SHA-256: `{casebook['input_identity_sha256']}`",
        "",
        "## Material cases",
        "",
        "| Case | Miner | Status | Severity | To epoch | Age (s) | Signals | Evidence |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    if not casebook["cases"]:
        lines.append("| _none_ | — | — | — | — | — | — | — |")
    for item in casebook["cases"]:
        evidence = f"{item['source_sha256'][:12]}…/{item['report_sha256'][:12]}…"
        lines.append(
            f"| `{item['case_id']}` | `{item['miner_id']}` | "
            f"{item['status']} | {item['severity']} | {item['to_epoch']} | "
            f"{item['age_seconds_floor']} | {', '.join(item['signals'])} | "
            f"`{evidence}` |"
        )
        if item["operator_note"]:
            lines.append(
                f"| ↳ note |  |  |  |  |  | "
                f"{item['operator_note'].replace('|', '&#124;').replace(chr(10), ' ')} |  |"
            )
    lines += [
        "",
        "## Authority boundary",
        "",
        "This casebook is verified operational triage evidence only. It does not "
        "infer expected rewards, assert that any payout is owed, resolve cases "
        "automatically, contact any provider or customer, mutate a node or "
        "wallet, submit a bounty, accept payment, or recognize revenue.",
        "",
    ]
    return "\n".join(lines)


def load_manifest(path: str | Path) -> tuple[
    list[tuple[Mapping[str, Any], Mapping[str, Any]]],
    list[Mapping[str, Any]],
    str,
]:
    manifest_path = Path(path).expanduser().absolute()
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise CasebookError("manifest must be an existing regular, non-symlink file")
    try:
        manifest = load_json_strict(manifest_path)
    except (OSError, ReconciliationError) as exc:
        raise CasebookError(f"could not load manifest: {exc}") from exc
    if type(manifest) is not dict or set(manifest) != MANIFEST_KEYS:
        keys = set(manifest) if type(manifest) is dict else set()
        raise CasebookError(
            "manifest keys mismatch; "
            f"missing={sorted(MANIFEST_KEYS - keys)}, "
            f"unknown={sorted(keys - MANIFEST_KEYS)}"
        )
    if manifest["schema_version"] != MANIFEST_SCHEMA:
        raise CasebookError(f"manifest schema_version must equal {MANIFEST_SCHEMA}")
    as_of, as_of_dt = _utc(manifest["as_of"], "manifest.as_of")
    normalize_dispositions(manifest["dispositions"], as_of_dt)
    dispositions = manifest["dispositions"]

    entries = manifest["entries"]
    if type(entries) is not list or not entries:
        raise CasebookError("manifest.entries must be a non-empty list")
    if len(entries) > 1000:
        raise CasebookError("manifest.entries exceeds 1000-entry safety bound")
    pairs = []
    base = manifest_path.parent
    seen_paths: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        field = f"manifest.entries[{index}]"
        if type(entry) is not dict or set(entry) != ENTRY_KEYS:
            keys = set(entry) if type(entry) is dict else set()
            raise CasebookError(
                f"{field} keys mismatch; "
                f"missing={sorted(ENTRY_KEYS - keys)}, "
                f"unknown={sorted(keys - ENTRY_KEYS)}"
            )
        source_path = _safe_input_path(base, entry["source"], f"{field}.source")
        artifact_path = _safe_input_path(base, entry["artifact"], f"{field}.artifact")
        path_key = (str(source_path), str(artifact_path))
        if path_key in seen_paths:
            raise CasebookError(f"duplicate manifest entry at {index}")
        seen_paths.add(path_key)
        try:
            source = load_json_strict(source_path)
            artifact = load_json_strict(artifact_path)
        except (OSError, ReconciliationError) as exc:
            raise CasebookError(f"{field} could not be loaded: {exc}") from exc
        pairs.append((source, artifact))
    return pairs, dispositions, as_of


def _write_new(path: str | Path, text: str) -> None:
    target = Path(path)
    try:
        with open(target, "x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
    except FileExistsError as exc:
        raise CasebookError(f"refusing to overwrite existing file: {target}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile verified reward reconciliations into an exception casebook."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    compile_p = sub.add_parser("compile")
    compile_p.add_argument("manifest")
    compile_p.add_argument("--json-out", required=True)
    compile_p.add_argument("--markdown-out", required=True)
    verify_p = sub.add_parser("verify")
    verify_p.add_argument("manifest")
    verify_p.add_argument("artifact")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        pairs, dispositions, as_of = load_manifest(args.manifest)
        if args.command == "compile":
            artifact = compile_casebook(
                pairs,
                dispositions=dispositions,
                as_of=as_of,
            )
            _write_new(
                args.json_out,
                json.dumps(
                    artifact,
                    sort_keys=True,
                    indent=2,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n",
            )
            _write_new(args.markdown_out, artifact["markdown"])
            return 0
        candidate = load_json_strict(args.artifact)
        ok = verify_casebook(
            pairs,
            candidate,
            dispositions=dispositions,
            as_of=as_of,
        )
        print(json.dumps({"ok": ok}, sort_keys=True))
        return 0 if ok else 1
    except (CasebookError, ReconciliationError, OSError, TypeError, ValueError) as exc:
        print(f"reward-casebook: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
