#!/usr/bin/env python3
"""Deterministic, evidence-only reconciliation for RustChain miner reward observations."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

SOURCE_SCHEMA = "rustchain.reward-observations/v1"
REPORT_SCHEMA = "rustchain.reward-reconciliation/v1"
RECEIPT_SCHEMA = "rustchain.reward-reconciliation-receipt/v1"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
MINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_KEYS = {"schema_version", "miner_id", "observations"}
OBS_KEYS = {"observed_at", "epoch", "balance_rtc"}
MAX_DECIMAL_DIGITS = 256
MAX_DECIMAL_EXPONENT = 128
MAX_EPOCH = (1 << 63) - 1
MAX_JSON_BYTES = 64 * 1024 * 1024
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
    """Malformed, ambiguous, or unsafe reconciliation evidence."""


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ReconciliationError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _stable_file_snapshot(before: os.stat_result, after: os.stat_result) -> bool:
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    return all(getattr(before, field, None) == getattr(after, field, None) for field in fields)


def _read_regular_json_bytes(path: str | Path) -> bytes:
    source = Path(path).expanduser().absolute()
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    try:
        path_info = source.lstat()
    except OSError as exc:
        raise ReconciliationError(f"cannot inspect JSON input {source}: {exc}") from exc

    attrs = getattr(path_info, "st_file_attributes", 0)
    if stat.S_ISLNK(path_info.st_mode) or (reparse and attrs & reparse):
        raise ReconciliationError(f"JSON input must be a regular non-symlink file: {source}")
    if not stat.S_ISREG(path_info.st_mode):
        raise ReconciliationError(f"JSON input must be a regular file: {source}")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EMLINK}:
            raise ReconciliationError(f"JSON input must be a regular non-symlink file: {source}") from exc
        raise ReconciliationError(f"cannot open JSON input {source}: {exc}") from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ReconciliationError(f"JSON input must be a regular file: {source}")
        if not _stable_file_snapshot(path_info, before):
            raise ReconciliationError(f"JSON input changed before descriptor binding: {source}")
        if before.st_size > MAX_JSON_BYTES:
            raise ReconciliationError(
                f"JSON input exceeds {MAX_JSON_BYTES}-byte safety bound: {source}"
            )

        chunks: list[bytes] = []
        remaining = MAX_JSON_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(fd)
        try:
            path_after = source.lstat()
        except OSError as exc:
            raise ReconciliationError(f"JSON input path changed while being read: {source}") from exc
    except ReconciliationError:
        raise
    except OSError as exc:
        raise ReconciliationError(f"cannot read JSON input {source}: {exc}") from exc
    finally:
        os.close(fd)

    after_attrs = getattr(path_after, "st_file_attributes", 0)
    if stat.S_ISLNK(path_after.st_mode) or (reparse and after_attrs & reparse):
        raise ReconciliationError(f"JSON input path changed while being read: {source}")
    if len(payload) > MAX_JSON_BYTES:
        raise ReconciliationError(f"JSON input exceeds {MAX_JSON_BYTES}-byte safety bound: {source}")
    if (
        len(payload) != before.st_size
        or not _stable_file_snapshot(before, after)
        or not _stable_file_snapshot(path_info, path_after)
    ):
        raise ReconciliationError(f"JSON input changed while being read: {source}")
    return payload


def load_json_strict(path: str | Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ReconciliationError(f"non-finite JSON number is forbidden: {value}")

    payload = _read_regular_json_bytes(path)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReconciliationError(f"JSON input is not valid UTF-8: {exc}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_object,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=reject_constant,
        )
    except ReconciliationError:
        raise
    except ValueError as exc:
        raise ReconciliationError(f"invalid JSON: {exc}") from exc


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ReconciliationError(f"{field} must be a finite decimal, not boolean/null")
    try:
        if isinstance(value, Decimal):
            out = value
        elif type(value) is int:
            out = Decimal(value)
        elif type(value) is float:
            if not math.isfinite(value):
                raise ReconciliationError(f"{field} must be finite")
            out = Decimal(str(value))
        elif type(value) is str and value and value.strip() == value:
            out = Decimal(value)
        else:
            raise ReconciliationError(f"{field} must be a decimal-compatible scalar")
    except InvalidOperation as exc:
        raise ReconciliationError(f"{field} must be a decimal") from exc
    if not out.is_finite():
        raise ReconciliationError(f"{field} must be finite")
    tup = out.as_tuple()
    if len(tup.digits) > MAX_DECIMAL_DIGITS or abs(tup.exponent) > MAX_DECIMAL_EXPONENT:
        raise ReconciliationError(f"{field} exceeds decimal precision/exponent safety bounds")
    if out and abs(out.adjusted()) > MAX_DECIMAL_EXPONENT:
        raise ReconciliationError(f"{field} exceeds decimal magnitude safety bounds")
    return out


def _dec(value: Decimal) -> str:
    if not value.is_finite():
        raise ReconciliationError("cannot canonicalize non-finite decimal")
    if not value:
        return "0"
    rendered = format(value.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _epoch(value: Any, field: str) -> int:
    if type(value) is not int:
        raise ReconciliationError(f"{field} must be an integer")
    if value < 0:
        raise ReconciliationError(f"{field} must be >= 0")
    if value > MAX_EPOCH:
        raise ReconciliationError(f"{field} exceeds supported epoch range")
    return value


def _utc(value: Any, field: str) -> tuple[str, datetime]:
    if type(value) is not str or not UTC_RE.fullmatch(value):
        raise ReconciliationError(f"{field} must be UTC in YYYY-MM-DDTHH:MM:SS[.ffffff]Z form")
    try:
        dt = datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise ReconciliationError(f"{field} is not a valid UTC timestamp") from exc
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    if dt.microsecond:
        base += "." + f"{dt.microsecond:06d}".rstrip("0")
    return base + "Z", dt


def normalize_source(source: Mapping[str, Any]) -> dict[str, Any]:
    if type(source) is not dict:
        raise ReconciliationError("source must be a JSON object")
    if set(source) != SOURCE_KEYS:
        raise ReconciliationError(
            f"source keys mismatch; missing={sorted(SOURCE_KEYS - set(source))}, "
            f"unknown={sorted(set(source) - SOURCE_KEYS)}"
        )
    if source["schema_version"] != SOURCE_SCHEMA:
        raise ReconciliationError(f"schema_version must equal {SOURCE_SCHEMA}")
    miner_id = source["miner_id"]
    if type(miner_id) is not str or not MINER_RE.fullmatch(miner_id):
        raise ReconciliationError("miner_id is malformed")
    rows = source["observations"]
    if type(rows) is not list or len(rows) < 2:
        raise ReconciliationError("observations must be a list with at least two entries")
    if len(rows) > 100_000:
        raise ReconciliationError("observations exceeds 100000-entry safety bound")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    prior: datetime | None = None
    for index, row in enumerate(rows):
        if type(row) is not dict or set(row) != OBS_KEYS:
            keys = set(row) if type(row) is dict else set()
            raise ReconciliationError(
                f"observations[{index}] keys mismatch; missing={sorted(OBS_KEYS - keys)}, "
                f"unknown={sorted(keys - OBS_KEYS)}"
            )
        observed_at, dt = _utc(row["observed_at"], f"observations[{index}].observed_at")
        if observed_at in seen:
            raise ReconciliationError(f"duplicate observation time: {observed_at}")
        if prior is not None and dt <= prior:
            raise ReconciliationError("observations must be in strictly increasing UTC order")
        balance = _decimal(row["balance_rtc"], f"observations[{index}].balance_rtc")
        if balance < 0:
            raise ReconciliationError(f"observations[{index}].balance_rtc must be >= 0")
        normalized.append({
            "observed_at": observed_at,
            "epoch": _epoch(row["epoch"], f"observations[{index}].epoch"),
            "balance_rtc": _dec(balance),
        })
        seen.add(observed_at)
        prior = dt
    return {"schema_version": SOURCE_SCHEMA, "miner_id": miner_id, "observations": normalized}


def source_from_history_db(db_path: str | Path, miner_id: str) -> dict[str, Any]:
    """Read the monitor's existing miner_history table without write authority."""
    if type(miner_id) is not str or not MINER_RE.fullmatch(miner_id):
        raise ReconciliationError("miner_id is malformed")
    db = Path(db_path).expanduser().absolute()
    if db.is_symlink() or not db.is_file():
        raise ReconciliationError("history DB must be an existing regular, non-symlink file")
    try:
        conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
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
            "SELECT id, miner_id, observed_at, epoch, balance_rtc FROM miner_history "
            "WHERE miner_id = ? ORDER BY observed_at ASC, id ASC",
            (miner_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise ReconciliationError(f"cannot read miner_history: {exc}") from exc
    finally:
        conn.close()
    if len(rows) < 2:
        raise ReconciliationError("history DB needs at least two snapshots for the requested miner")

    observations = []
    for index, row in enumerate(rows):
        if row["epoch"] is None:
            raise ReconciliationError(f"history row {index} has no epoch")
        timestamp = _decimal(row["observed_at"], f"history[{index}].observed_at")
        try:
            dt = datetime.fromtimestamp(float(timestamp), timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ReconciliationError(f"history[{index}].observed_at is outside UTC range") from exc
        observed_at = dt.strftime("%Y-%m-%dT%H:%M:%S")
        if dt.microsecond:
            observed_at += "." + f"{dt.microsecond:06d}".rstrip("0")
        balance = _decimal(row["balance_rtc"], f"history[{index}].balance_rtc")
        observations.append({
            "observed_at": observed_at + "Z",
            "epoch": _epoch(row["epoch"], f"history[{index}].epoch"),
            "balance_rtc": _dec(balance),
        })
    return normalize_source({"schema_version": SOURCE_SCHEMA, "miner_id": miner_id, "observations": observations})


def _json_bytes(value: Any) -> bytes:
    def ready(item: Any) -> Any:
        if isinstance(item, Decimal):
            return _dec(item)
        if isinstance(item, dict):
            return {k: ready(v) for k, v in item.items()}
        if isinstance(item, list):
            return [ready(v) for v in item]
        return item
    return json.dumps(ready(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _transition(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    pe, ce = int(previous["epoch"]), int(current["epoch"])
    pb, cb = Decimal(str(previous["balance_rtc"])), Decimal(str(current["balance_rtc"]))
    ed, bd = ce - pe, cb - pb
    signals: list[str] = []
    severity = "INFO"
    if ed < 0:
        signals.append("EPOCH_REGRESSION")
        severity = "ANOMALY"
    if bd < 0:
        signals.append("BALANCE_REGRESSION")
        severity = "ANOMALY"
    if ed == 0:
        signals.append("SAME_EPOCH_STABLE" if bd == 0 else "SAME_EPOCH_BALANCE_CONFLICT")
        if bd != 0:
            severity = "ANOMALY"
    elif ed > 0:
        if ed > 1:
            signals.append("OBSERVATION_GAP")
            if severity != "ANOMALY":
                severity = "CAUTION"
        if bd == 0:
            signals.append("EPOCH_ADVANCE_NO_OBSERVED_GAIN")
            if severity != "ANOMALY":
                severity = "CAUTION"
        elif bd > 0:
            signals.append("POSITIVE_OBSERVED_GAIN")
    if not signals:
        signals.append("UNCLASSIFIED_TRANSITION")
        severity = "CAUTION"
    return {
        "from_observed_at": previous["observed_at"], "to_observed_at": current["observed_at"],
        "from_epoch": pe, "to_epoch": ce, "epoch_delta": ed,
        "from_balance_rtc": _dec(pb), "to_balance_rtc": _dec(cb),
        "balance_delta_rtc": _dec(bd), "severity": severity, "signals": signals,
    }


def _markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# RustChain reward reconciliation", "",
        f"- Miner: `{report['miner_id']}`",
        f"- Source observations: {report['observation_count']}",
        f"- Source SHA-256: `{report['source_sha256']}`",
        f"- Window: `{report['first_observed_at']}` → `{report['last_observed_at']}`",
        f"- Transitions: {summary['transition_count']}",
        f"- Anomaly transitions: {summary['anomaly_transition_count']}",
        f"- Caution transitions: {summary['caution_transition_count']}",
        f"- Informational transitions: {summary['info_transition_count']}", "",
        "## Transition evidence", "",
        "| From epoch | To epoch | Δ epoch | Δ RTC | Severity | Signals |",
        "| ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for item in report["transitions"]:
        lines.append(
            f"| {item['from_epoch']} | {item['to_epoch']} | {item['epoch_delta']} | "
            f"{item['balance_delta_rtc']} | {item['severity']} | {', '.join(item['signals'])} |"
        )
    lines += [
        "", "## Authority boundary", "",
        "This artifact is diagnostic evidence only. It does not infer an expected reward rate, "
        "assert that any payout is owed, contact any party, mutate a node or wallet, submit a "
        "bounty, transfer RTC, or recognize revenue.", "",
    ]
    return "\n".join(lines)


def compile_reconciliation(source: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_source(source)
    rows = normalized["observations"]
    transitions = [_transition(rows[i - 1], rows[i]) for i in range(1, len(rows))]
    signals = sorted({signal for item in transitions for signal in item["signals"]})
    summary = {
        "transition_count": len(transitions),
        "anomaly_transition_count": sum(x["severity"] == "ANOMALY" for x in transitions),
        "caution_transition_count": sum(x["severity"] == "CAUTION" for x in transitions),
        "info_transition_count": sum(x["severity"] == "INFO" for x in transitions),
        "signal_counts": {s: sum(s in x["signals"] for x in transitions) for s in signals},
    }
    report = {
        "schema_version": REPORT_SCHEMA, "miner_id": normalized["miner_id"],
        "source_schema_version": SOURCE_SCHEMA, "source_sha256": _sha(normalized),
        "observation_count": len(rows), "first_observed_at": rows[0]["observed_at"],
        "last_observed_at": rows[-1]["observed_at"], "summary": summary,
        "transitions": transitions, "authority": dict(AUTHORITY),
    }
    markdown = _markdown(report)
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "source_sha256": report["source_sha256"],
        "report_sha256": _sha(report),
        "markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest(),
    }
    return {"report": report, "markdown": markdown, "receipt": receipt}


def verify_reconciliation(source: Mapping[str, Any], artifact: Mapping[str, Any]) -> bool:
    if type(artifact) is not dict or set(artifact) != {"report", "markdown", "receipt"}:
        return False
    try:
        expected = compile_reconciliation(source)
    except ReconciliationError:
        return False
    if artifact != expected:
        return False
    receipt = artifact.get("receipt")
    if type(receipt) is not dict or set(receipt) != {"schema_version", "source_sha256", "report_sha256", "markdown_sha256"}:
        return False
    return receipt.get("schema_version") == RECEIPT_SCHEMA and all(
        type(receipt.get(key)) is str and HEX64_RE.fullmatch(receipt[key]) is not None
        for key in ("source_sha256", "report_sha256", "markdown_sha256")
    )


def _plain_output(path: str | Path) -> Path:
    target = Path(path).expanduser().absolute()
    parent = target.parent
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    current = Path(parent.anchor)
    for part in (parent.parts[1:] if parent.anchor else parent.parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir()
            info = current.lstat()
        attrs = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or (reparse and attrs & reparse):
            raise ReconciliationError(f"output parent must not traverse links: {current}")
        if not stat.S_ISDIR(info.st_mode):
            raise ReconciliationError(f"output parent component is not a directory: {current}")
    return target


def _write_new(path: str | Path, content: str) -> None:
    target = _plain_output(path)
    if target.exists() or target.is_symlink():
        raise ReconciliationError(f"refusing to overwrite output path: {target}")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
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
    return json.dumps(artifact, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    compile_p = sub.add_parser("compile")
    compile_p.add_argument("source")
    history_p = sub.add_parser("compile-history")
    history_p.add_argument("history_db")
    history_p.add_argument("--miner-id", required=True)
    for item in (compile_p, history_p):
        item.add_argument("--json-out", required=True)
        item.add_argument("--markdown-out", required=True)
    verify_p = sub.add_parser("verify")
    verify_p.add_argument("source")
    verify_p.add_argument("artifact")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        source = source_from_history_db(args.history_db, args.miner_id) if args.command == "compile-history" else load_json_strict(args.source)
        if args.command in {"compile", "compile-history"}:
            artifact = compile_reconciliation(source)
            _write_new(args.json_out, _artifact_json(artifact))
            _write_new(args.markdown_out, artifact["markdown"])
            print(json.dumps({
                "ok": True, "miner_id": artifact["report"]["miner_id"],
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
