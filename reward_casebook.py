#!/usr/bin/env python3
"""Compile verified RustChain reward reconciliations into an operator exception casebook.

This module is evidence-only. It verifies every reconciliation by fully recompiling
it from the original source observations, then promotes only CAUTION / ANOMALY
transitions into stable operator cases. It never infers payout entitlement,
expected reward, customer acceptance, or revenue.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from reward_reconciliation import ReconciliationError, load_json_strict, verify_reconciliation

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
MAX_INPUT_BYTES = 64 * 1024 * 1024
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
    if not [part for part in path.parts if part not in ("", ".")]:
        raise CasebookError(f"{field} must name a regular file")
    return path


def _secure_open_supported() -> bool:
    return (
        os.name == "posix"
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
    )


def _open_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    if directory:
        flags |= os.O_DIRECTORY
    return flags


def _open_child(parent_fd: int, name: str, *, directory: bool, field: str) -> int:
    try:
        return os.open(name, _open_flags(directory=directory), dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise CasebookError(f"{field} must not traverse symlinks") from exc
        if directory and exc.errno in (errno.ENOTDIR, errno.ENOENT):
            try:
                entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError:
                entry = None
            if entry is not None and stat.S_ISLNK(entry.st_mode):
                raise CasebookError(f"{field} must not traverse symlinks") from exc
            raise CasebookError(f"{field} has a missing or non-directory component") from exc
        if not directory and exc.errno in (errno.ENOENT, errno.ENOTDIR):
            raise CasebookError(f"{field} must resolve to an existing regular file") from exc
        raise CasebookError(f"could not open {field}: {exc}") from exc


def _open_absolute_parent(path: Path, field: str) -> tuple[int, str]:
    if not _secure_open_supported():
        raise CasebookError(
            "secure descriptor-relative input loading is unsupported on this platform"
        )
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    parts = [part for part in absolute.parts if part not in (absolute.anchor, "", ".")]
    if not parts:
        raise CasebookError(f"{field} must name a regular file")
    try:
        current_fd = os.open(absolute.anchor or os.sep, _open_flags(directory=True))
    except OSError as exc:
        raise CasebookError(f"could not open filesystem root for {field}: {exc}") from exc
    try:
        for part in parts[:-1]:
            next_fd = _open_child(current_fd, part, directory=True, field=field)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, parts[-1]
    except Exception:
        os.close(current_fd)
        raise


def _open_relative_parent(root_fd: int, relative: Path, field: str) -> tuple[int, str]:
    parts = [part for part in relative.parts if part not in ("", ".")]
    if not parts:
        raise CasebookError(f"{field} must name a regular file")
    current_fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = _open_child(current_fd, part, directory=True, field=field)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, parts[-1]
    except Exception:
        os.close(current_fd)
        raise


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_fd_bytes(fd: int, field: str) -> bytes:
    """Read one already-validated descriptor. Kept separate for race hostiles."""
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = os.read(fd, min(1024 * 1024, MAX_INPUT_BYTES + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_INPUT_BYTES:
            raise CasebookError(f"{field} exceeds {MAX_INPUT_BYTES}-byte safety bound")
    return b"".join(chunks)


def _strict_json_bytes(data: bytes, field: str) -> Any:
    def object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise CasebookError(f"{field} has duplicate JSON key: {key}")
            out[key] = value
        return out

    def reject_constant(value: str) -> None:
        raise CasebookError(f"{field} contains non-finite JSON number: {value}")

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CasebookError(f"{field} must be UTF-8 JSON") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=object_no_duplicates,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CasebookError(f"{field} is not valid JSON: {exc}") from exc


def _read_regular_json_at(
    parent_fd: int,
    name: str,
    field: str,
) -> tuple[Any, tuple[int, int]]:
    file_fd = _open_child(parent_fd, name, directory=False, field=field)
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise CasebookError(f"{field} must resolve to an existing regular file")
        if before.st_size > MAX_INPUT_BYTES:
            raise CasebookError(f"{field} exceeds {MAX_INPUT_BYTES}-byte safety bound")
        data = _read_fd_bytes(file_fd, field)
        after = os.fstat(file_fd)
        if _stat_signature(after) != _stat_signature(before):
            raise CasebookError(f"{field} changed while it was being read")
        try:
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise CasebookError(f"{field} changed while it was being read") from exc
        if (
            not stat.S_ISREG(named.st_mode)
            or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise CasebookError(f"{field} changed while it was being read")
        return _strict_json_bytes(data, field), (before.st_dev, before.st_ino)
    finally:
        os.close(file_fd)


def _read_absolute_json(path: str | Path, field: str) -> Any:
    parent_fd, name = _open_absolute_parent(Path(path), field)
    try:
        value, _ = _read_regular_json_at(parent_fd, name, field)
        return value
    finally:
        os.close(parent_fd)


def _read_relative_json(
    root_fd: int,
    relative: Path,
    field: str,
) -> tuple[Any, tuple[int, int]]:
    parent_fd, name = _open_relative_parent(root_fd, relative, field)
    try:
        return _read_regular_json_at(parent_fd, name, field)
    finally:
        os.close(parent_fd)


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
        status_value = item["status"]
        if status_value not in DISPOSITION_STATUSES:
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
            "status": status_value,
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
        [] if dispositions is None else list(dispositions), as_of_dt
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
            status_value = disposition["status"] if disposition else "OPEN"
            updated_at = disposition["updated_at"] if disposition else None
            note = disposition["note"] if disposition else ""
            if updated_at is not None:
                _, updated_dt = _utc(updated_at, f"disposition[{case_id}].updated_at")
                _, observed_dt = _utc(
                    transition["to_observed_at"], f"case[{case_id}].to_observed_at"
                )
                if updated_dt < observed_dt:
                    raise CasebookError(
                        f"disposition for {case_id} predates the case observation"
                    )

            cases.append(
                {
                    "case_id": case_id,
                    "miner_id": report["miner_id"],
                    "status": status_value,
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
            evidence_pairs, dispositions=dispositions, as_of=as_of
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
    manifest_path = Path(path).expanduser()
    root_fd, manifest_name = _open_absolute_parent(manifest_path, "manifest")
    try:
        manifest, _ = _read_regular_json_at(root_fd, manifest_name, "manifest")
        if type(manifest) is not dict or set(manifest) != MANIFEST_KEYS:
            keys = set(manifest) if type(manifest) is dict else set()
            raise CasebookError(
                "manifest keys mismatch; "
                f"missing={sorted(MANIFEST_KEYS - keys)}, "
                f"unknown={sorted(keys - MANIFEST_KEYS)}"
            )
        if manifest["schema_version"] != MANIFEST_SCHEMA:
            raise CasebookError(
                f"manifest schema_version must equal {MANIFEST_SCHEMA}"
            )
        as_of, as_of_dt = _utc(manifest["as_of"], "manifest.as_of")
        normalize_dispositions(manifest["dispositions"], as_of_dt)
        dispositions = manifest["dispositions"]

        entries = manifest["entries"]
        if type(entries) is not list or not entries:
            raise CasebookError("manifest.entries must be a non-empty list")
        if len(entries) > 1000:
            raise CasebookError("manifest.entries exceeds 1000-entry safety bound")
        pairs = []
        seen_paths: set[tuple[str, str]] = set()
        seen_files: set[tuple[tuple[int, int], tuple[int, int]]] = set()
        for index, entry in enumerate(entries):
            field = f"manifest.entries[{index}]"
            if type(entry) is not dict or set(entry) != ENTRY_KEYS:
                keys = set(entry) if type(entry) is dict else set()
                raise CasebookError(
                    f"{field} keys mismatch; "
                    f"missing={sorted(ENTRY_KEYS - keys)}, "
                    f"unknown={sorted(keys - ENTRY_KEYS)}"
                )
            source_relative = _relative_path(entry["source"], f"{field}.source")
            artifact_relative = _relative_path(entry["artifact"], f"{field}.artifact")
            path_identity = (str(source_relative), str(artifact_relative))
            if path_identity in seen_paths:
                raise CasebookError(f"duplicate manifest entry at {index}")
            seen_paths.add(path_identity)
            source, source_identity = _read_relative_json(
                root_fd, source_relative, f"{field}.source"
            )
            artifact, artifact_identity = _read_relative_json(
                root_fd, artifact_relative, f"{field}.artifact"
            )
            identity = (source_identity, artifact_identity)
            if identity in seen_files:
                raise CasebookError(f"duplicate manifest entry at {index}")
            seen_files.add(identity)
            pairs.append((source, artifact))
        return pairs, dispositions, as_of
    finally:
        os.close(root_fd)


def _write_new(path: str | Path, text: str) -> Path:
    target = Path(path)
    try:
        with open(target, "x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
    except FileExistsError as exc:
        raise CasebookError(f"refusing to overwrite existing file: {target}") from exc
    return target


def _write_outputs(
    json_path: str | Path,
    json_text: str,
    markdown_path: str | Path,
    markdown_text: str,
) -> None:
    json_target = Path(json_path)
    markdown_target = Path(markdown_path)
    if json_target.absolute() == markdown_target.absolute():
        raise CasebookError("JSON and Markdown outputs must be different files")
    for target in (json_target, markdown_target):
        if target.exists() or target.is_symlink():
            raise CasebookError(f"refusing to overwrite existing file: {target}")

    _write_new(json_target, json_text)
    try:
        _write_new(markdown_target, markdown_text)
    except Exception as exc:
        raise CasebookError(
            f"{exc}; one or both output paths may remain; no rollback was "
            "attempted because either pathname may have been replaced concurrently"
        ) from exc


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
                pairs, dispositions=dispositions, as_of=as_of
            )
            json_text = (
                json.dumps(
                    artifact,
                    sort_keys=True,
                    indent=2,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            )
            _write_outputs(
                args.json_out,
                json_text,
                args.markdown_out,
                artifact["markdown"],
            )
            return 0
        candidate = _read_absolute_json(args.artifact, "casebook artifact")
        ok = verify_casebook(
            pairs, candidate, dispositions=dispositions, as_of=as_of
        )
        print(json.dumps({"ok": ok}, sort_keys=True))
        return 0 if ok else 1
    except (CasebookError, ReconciliationError, OSError, TypeError, ValueError) as exc:
        print(f"reward-casebook: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
