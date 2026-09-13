#!/usr/bin/env python3
"""Deterministic, evidence-only reconciliation for RustChain miner reward observations."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

SOURCE_SCHEMA = "rustchain.reward-observations/v1"
REPORT_SCHEMA = "rustchain.reward-reconciliation/v1"
RECEIPT_SCHEMA = "rustchain.reward-reconciliation-receipt/v1"
UTC_SECOND_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
MINER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
OBSERVATION_KEYS = frozenset({"observed_at", "epoch", "balance_rtc"})
SOURCE_KEYS = frozenset({"schema_version", "miner_id", "observations"})
AUTHORITY = {
    "payout_owed": False,
    "expected_reward_inferred": False,
    "node_mutation": False,
    "wallet_mutation": False,
    "payout_mutation": False,
    "bounty_submission": False,
    "rtc_transfer": False,
    "payment_acceptance": False,
    "revenue_recognition": False,
}


class ReconciliationError(ValueError):
    """Raised when source or artifact evidence is malformed or ambiguous."""


def _duplicate_key_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReconciliationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json_strict(path: str | Path) -> Any:
    """Load JSON while rejecting duplicate keys and non-finite numeric constants."""
    def reject_constant(value: str) -> None:
        raise ReconciliationError(f"non-finite JSON number is forbidden: {value}")

    with open(Path(path), "r", encoding="utf-8") as handle:
        return json.load(
            handle,
            object_pairs_hook=_duplicate_key_guard,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=reject_constant,
        )


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ReconciliationError(f"{field} must be a finite decimal, not boolean/null")
    if isinstance(value, Decimal):
        parsed = value
    elif type(value) is int:
        parsed = Decimal(value)
    elif type(value) is float:
        if not math.isfinite(value):
            raise ReconciliationError(f"{field} must be finite")
        parsed = Decimal(str(value))
    elif type(value) is str:
        if not value or value.strip() != value:
            raise ReconciliationError(f"{field} decimal string must not contain surrounding whitespace")
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ReconciliationError(f"{field} must be a decimal") from exc
    else:
        raise ReconciliationError(f"{field} must be a decimal-compatible scalar")
    if not parsed.is_finite():
        raise ReconciliationError(f"{field} must be finite")
    return parsed


def _canonical_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ReconciliationError("cannot canonicalize non-finite decimal")
    if value == 0:
        return "0"
    normalized = value.normalize()
    rendered = format(normalized, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _strict_int(value: Any, field: str) -> int:
    if type(value) is not int:
        raise ReconciliationError(f"{field} must be an integer")
    if value < 0:
        raise ReconciliationError(f"{field} must be >= 0")
    return value


def _utc_second(value: Any, field: str) -> tuple[str, datetime]:
    if type(value) is not str or not UTC_SECOND_RE.fullmatch(value):
        raise ReconciliationError(
            f"{field} must be UTC in YYYY-MM-DDTHH:MM:SS[.ffffff]Z form"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise ReconciliationError(f"{field} is not a valid UTC timestamp") from exc
    if parsed.microsecond:
        fraction = f"{parsed.microsecond:06d}".rstrip("0")
        canonical = parsed.strftime("%Y-%m-%dT%H:%M:%S") + f".{fraction}Z"
    else:
        canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    return canonical, parsed


def source_from_history_db(db_path: str | Path, miner_id: str) -> dict[str, Any]:
    """Build strict reconciliation source evidence from the monitor's existing SQLite history DB."""
    if type(miner_id) is not str or not MINER_ID_RE.fullmatch(miner_id):
        raise ReconciliationError("miner_id is malformed")
    db_file = Path(db_path).expanduser().absolute()
    if db_file.is_symlink() or not db_file.is_file():
        raise ReconciliationError("history DB must be an existing regular, non-symlink file")

    uri = f"file:{db_file.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ReconciliationError(f"cannot open history DB read-only: {exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(miner_history)").fetchall()}
        required = {"id", "miner_id", "observed_at", "epoch", "balance_rtc"}
        if not required.issubset(columns):
            raise ReconciliationError(
                f"miner_history schema missing required columns: {sorted(required - columns)}"
            )
        rows = conn.execute(
            """
            SELECT id, miner_id, observed_at, epoch, balance_rtc
            FROM miner_history
            WHERE miner_id = ?
            ORDER BY observed_at ASC, id ASC
            """,
            (miner_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise ReconciliationError(f"cannot read miner_history: {exc}") from exc
    finally:
        conn.close()

    if len(rows) < 2:
        raise ReconciliationError("history DB needs at least two snapshots for the requested miner")

    observations = []
    for index, item in enumerate(rows):
        if item["epoch"] is None:
            raise ReconciliationError(f"history row {index} has no epoch")
        epoch = _strict_int(item["epoch"], f"history[{index}].epoch")
        observed = _decimal(item["observed_at"], f"history[{index}].observed_at")
        try:
            observed_dt = datetime.fromtimestamp(float(observed), timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ReconciliationError(f"history[{index}].observed_at is outside UTC range") from exc
        if observed_dt.microsecond:
            fraction = f"{observed_dt.microsecond:06d}".rstrip("0")
            observed_at = observed_dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{fraction}Z"
        else:
            observed_at = observed_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        balance = _decimal(item["balance_rtc"], f"history[{index}].balance_rtc")
        observations.append({
            "observed_at": observed_at,
            "epoch": epoch,
            "balance_rtc": _canonical_decimal(balance),
        })

    return normalize_source({
        "schema_version": SOURCE_SCHEMA,
        "miner_id": miner_id,
        "observations": observations,
    })


def _canonical_json_bytes(value: Any) -> bytes:
    def convert(item: Any) -> Any:
        if isinstance(item, Decimal):
            return _canonical_decimal(item)
        if isinstance(item, dict):
            return {key: convert(val) for key, val in item.items()}
        if isinstance(item, list):
            return [convert(val) for val in item]
        return item

    return json.dumps(
        convert(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True)
class Observation:
    observed_at: str
    observed_dt: datetime
    epoch: int
    balance: Decimal

    def canonical(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "epoch": self.epoch,
            "balance_rtc": _canonical_decimal(self.balance),
        }


def normalize_source(source: Mapping[str, Any]) -> dict[str, Any]:
    if type(source) is not dict:
        raise ReconciliationError("source must be a JSON object")
    if frozenset(source) != SOURCE_KEYS:
        unknown = sorted(set(source) - SOURCE_KEYS)
        missing = sorted(SOURCE_KEYS - set(source))
        raise ReconciliationError(f"source keys mismatch; missing={missing}, unknown={unknown}")
    if source.get("schema_version") != SOURCE_SCHEMA:
        raise ReconciliationError(f"schema_version must equal {SOURCE_SCHEMA}")

    miner_id = source.get("miner_id")
    if type(miner_id) is not str or not MINER_ID_RE.fullmatch(miner_id):
        raise ReconciliationError("miner_id is malformed")

    raw_observations = source.get("observations")
    if type(raw_observations) is not list or len(raw_observations) < 2:
        raise ReconciliationError("observations must be a list with at least two entries")
    if len(raw_observations) > 100_000:
        raise ReconciliationError("observations exceeds 100000-entry safety bound")

    observations: list[Observation] = []
    seen_times: set[str] = set()
    prior_dt: datetime | None = None
    for index, raw in enumerate(raw_observations):
        if type(raw) is not dict:
            raise ReconciliationError(f"observations[{index}] must be an object")
        if frozenset(raw) != OBSERVATION_KEYS:
            unknown = sorted(set(raw) - OBSERVATION_KEYS)
            missing = sorted(OBSERVATION_KEYS - set(raw))
            raise ReconciliationError(
                f"observations[{index}] keys mismatch; missing={missing}, unknown={unknown}"
            )
        observed_at, observed_dt = _utc_second(raw["observed_at"], f"observations[{index}].observed_at")
        if observed_at in seen_times:
            raise ReconciliationError(f"duplicate observation time: {observed_at}")
        if prior_dt is not None and observed_dt <= prior_dt:
            raise ReconciliationError("observations must be in strictly increasing UTC order")
        epoch = _strict_int(raw["epoch"], f"observations[{index}].epoch")
        balance = _decimal(raw["balance_rtc"], f"observations[{index}].balance_rtc")
        if balance < 0:
            raise ReconciliationError(f"observations[{index}].balance_rtc must be >= 0")
        observations.append(Observation(observed_at, observed_dt, epoch, balance))
        seen_times.add(observed_at)
        prior_dt = observed_dt

    return {
        "schema_version": SOURCE_SCHEMA,
        "miner_id": miner_id,
        "observations": [item.canonical() for item in observations],
    }


def _classify_transition(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    previous_epoch = int(previous["epoch"])
    current_epoch = int(current["epoch"])
    previous_balance = Decimal(str(previous["balance_rtc"]))
    current_balance = Decimal(str(current["balance_rtc"]))
    epoch_delta = current_epoch - previous_epoch
    balance_delta = current_balance - previous_balance

    signals: list[str] = []
    severity = "INFO"

    if epoch_delta < 0:
        signals.append("EPOCH_REGRESSION")
        severity = "ANOMALY"
    if balance_delta < 0:
        signals.append("BALANCE_REGRESSION")
        severity = "ANOMALY"

    if epoch_delta == 0:
        if balance_delta == 0:
            signals.append("SAME_EPOCH_STABLE")
        else:
            signals.append("SAME_EPOCH_BALANCE_CONFLICT")
            severity = "ANOMALY"
    elif epoch_delta > 0:
        if epoch_delta > 1:
            signals.append("OBSERVATION_GAP")
            if severity != "ANOMALY":
                severity = "CAUTION"
        if balance_delta == 0:
            signals.append("EPOCH_ADVANCE_NO_OBSERVED_GAIN")
            if severity != "ANOMALY":
                severity = "CAUTION"
        elif balance_delta > 0:
            signals.append("POSITIVE_OBSERVED_GAIN")
    if not signals:
        signals.append("UNCLASSIFIED_TRANSITION")
        severity = "CAUTION"

    return {
        "from_observed_at": previous["observed_at"],
        "to_observed_at": current["observed_at"],
        "from_epoch": previous_epoch,
        "to_epoch": current_epoch,
        "epoch_delta": epoch_delta,
        "from_balance_rtc": _canonical_decimal(previous_balance),
        "to_balance_rtc": _canonical_decimal(current_balance),
        "balance_delta_rtc": _canonical_decimal(balance_delta),
        "severity": severity,
        "signals": signals,
    }


def _render_markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# RustChain reward reconciliation",
        "",
        f"- Miner: `{report['miner_id']}`",
        f"- Source observations: {report['observation_count']}",
        f"- Source SHA-256: `{report['source_sha256']}`",
        f"- Window: `{report['first_observed_at']}` → `{report['last_observed_at']}`",
        f"- Transitions: {summary['transition_count']}",
        f"- Anomaly transitions: {summary['anomaly_transition_count']}",
        f"- Caution transitions: {summary['caution_transition_count']}",
        f"- Informational transitions: {summary['info_transition_count']}",
        "",
        "## Transition evidence",
        "",
        "| From epoch | To epoch | Δ epoch | Δ RTC | Severity | Signals |",
        "| ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for transition in report["transitions"]:
        signal_text = ", ".join(transition["signals"])
        lines.append(
            f"| {transition['from_epoch']} | {transition['to_epoch']} | "
            f"{transition['epoch_delta']} | {transition['balance_delta_rtc']} | "
            f"{transition['severity']} | {signal_text} |"
        )
    lines.extend(
        [
            "",
            "## Authority boundary",
            "",
            "This artifact is diagnostic evidence only. It does not infer an expected reward rate, "
            "assert that any payout is owed, contact any party, mutate a node or wallet, submit a "
            "bounty, transfer RTC, or recognize revenue.",
            "",
        ]
    )
    return "\n".join(lines)


def compile_reconciliation(source: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_source(source)
    observations = normalized["observations"]
    transitions = [
        _classify_transition(observations[index - 1], observations[index])
        for index in range(1, len(observations))
    ]
    summary = {
        "transition_count": len(transitions),
        "anomaly_transition_count": sum(item["severity"] == "ANOMALY" for item in transitions),
        "caution_transition_count": sum(item["severity"] == "CAUTION" for item in transitions),
        "info_transition_count": sum(item["severity"] == "INFO" for item in transitions),
        "signal_counts": {
            signal: sum(signal in item["signals"] for item in transitions)
            for signal in sorted({signal for item in transitions for signal in item["signals"]})
        },
    }
    report = {
        "schema_version": REPORT_SCHEMA,
        "miner_id": normalized["miner_id"],
        "source_schema_version": SOURCE_SCHEMA,
        "source_sha256": _sha256(normalized),
        "observation_count": len(observations),
        "first_observed_at": observations[0]["observed_at"],
        "last_observed_at": observations[-1]["observed_at"],
        "summary": summary,
        "transitions": transitions,
        "authority": dict(AUTHORITY),
    }
    markdown = _render_markdown(report)
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "source_sha256": report["source_sha256"],
        "report_sha256": _sha256(report),
        "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
    }
    return {"report": report, "markdown": markdown, "receipt": receipt}


def verify_reconciliation(source: Mapping[str, Any], artifact: Mapping[str, Any]) -> bool:
    if type(artifact) is not dict or frozenset(artifact) != {"report", "markdown", "receipt"}:
        return False
    try:
        expected = compile_reconciliation(source)
    except ReconciliationError:
        return False
    if artifact != expected:
        return False
    receipt = artifact.get("receipt")
    if type(receipt) is not dict or frozenset(receipt) != {
        "schema_version", "source_sha256", "report_sha256", "markdown_sha256"
    }:
        return False
    return (
        receipt.get("schema_version") == RECEIPT_SCHEMA
        and type(receipt.get("source_sha256")) is str
        and type(receipt.get("report_sha256")) is str
        and type(receipt.get("markdown_sha256")) is str
        and HEX64_RE.fullmatch(receipt["source_sha256"]) is not None
        and HEX64_RE.fullmatch(receipt["report_sha256"]) is not None
        and HEX64_RE.fullmatch(receipt["markdown_sha256"]) is not None
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _canonical_decimal(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _create_exclusive_text(path: str | Path, content: str) -> None:
    target = Path(path).expanduser()
    if target.exists() or target.is_symlink():
        raise ReconciliationError(f"refusing to overwrite output path: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise ReconciliationError(f"output parent must not be a symlink: {target.parent}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except Exception:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise


def _artifact_json(artifact: Mapping[str, Any]) -> str:
    return json.dumps(_json_ready(artifact), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    compile_parser = subparsers.add_parser("compile", help="compile a deterministic reconciliation artifact")
    compile_parser.add_argument("source", help="strict reward-observation JSON")
    compile_parser.add_argument("--json-out", required=True, help="create-exclusive artifact JSON path")
    compile_parser.add_argument("--markdown-out", required=True, help="create-exclusive Markdown path")
    history_parser = subparsers.add_parser(
        "compile-history", help="compile directly from the monitor's read-only SQLite history DB"
    )
    history_parser.add_argument("history_db", help="existing monitor history.db")
    history_parser.add_argument("--miner-id", required=True, help="exact miner identity")
    history_parser.add_argument("--json-out", required=True, help="create-exclusive artifact JSON path")
    history_parser.add_argument("--markdown-out", required=True, help="create-exclusive Markdown path")
    verify_parser = subparsers.add_parser("verify", help="fully recompile and verify an artifact")
    verify_parser.add_argument("source", help="strict reward-observation JSON")
    verify_parser.add_argument("artifact", help="compiled artifact JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "compile-history":
            source = source_from_history_db(args.history_db, args.miner_id)
        else:
            source = load_json_strict(args.source)
        if args.command in {"compile", "compile-history"}:
            artifact = compile_reconciliation(source)
            _create_exclusive_text(args.json_out, _artifact_json(artifact))
            _create_exclusive_text(args.markdown_out, artifact["markdown"])
            print(json.dumps({
                "ok": True,
                "miner_id": artifact["report"]["miner_id"],
                "source_sha256": artifact["report"]["source_sha256"],
                "report_sha256": artifact["receipt"]["report_sha256"],
            }, sort_keys=True))
            return 0
        artifact = load_json_strict(args.artifact)
        ok = verify_reconciliation(source, artifact)
        print(json.dumps({"ok": ok}, sort_keys=True))
        return 0 if ok else 3
    except (OSError, ReconciliationError, ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
