# Miner reward reconciliation

`reward_reconciliation.py` turns the monitor's recorded miner history into a deterministic, tamper-evident diagnostic artifact. It is intentionally evidence-only: it reports what the recorded observations show without inventing an expected reward rate or asserting that RustChain owes a payout.

## Directly from the existing history DB

If you already run the monitor with `--record-history`, compile a report from the same SQLite database in read-only mode:

```bash
python reward_reconciliation.py compile-history ~/.rustchain-monitor/history.db \
  --miner-id YOUR_MINER_ID \
  --json-out reward-report.json \
  --markdown-out reward-report.md
```

The adapter reads only `miner_history.id`, `miner_id`, `observed_at`, `epoch`, and `balance_rtc`. It refuses symlink DB paths, missing schema columns, missing epochs, malformed/nonfinite values, fewer than two snapshots, or ambiguous timestamp ordering. It opens SQLite with `mode=ro`; compilation never writes to the monitor database.

## Portable source JSON

You can also reconcile a standalone evidence file:

```json
{
  "schema_version": "rustchain.reward-observations/v1",
  "miner_id": "vintage-g4-mac",
  "observations": [
    {"observed_at": "2026-09-13T09:00:00Z", "epoch": 1847, "balance_rtc": "45.782500"},
    {"observed_at": "2026-09-13T09:10:00Z", "epoch": 1848, "balance_rtc": "46.157500"}
  ]
}
```

Compile it with:

```bash
python reward_reconciliation.py compile examples/reward-observations.example.json \
  --json-out reward-report.json \
  --markdown-out reward-report.md
```

Portable source files and verification artifacts must be stable, single-link regular UTF-8 files. The reader refuses hard links, final-component symlinks/reparse points, and special files; binds the opened descriptor to the inspected path; uses a mutation-sensitive descriptor generation token on POSIX and Windows; rejects mutation during the read; and enforces a 64 MiB byte ceiling before JSON parsing.

Output paths are create-exclusive. Existing outputs are never overwritten.

## What it classifies

Each adjacent observation pair is classified independently. The report can contain multiple signals for one transition:

- `POSITIVE_OBSERVED_GAIN` — the epoch advanced and the recorded balance increased.
- `SAME_EPOCH_STABLE` — same epoch and same balance.
- `OBSERVATION_GAP` — more than one epoch elapsed between recorded observations, so per-epoch attribution is not available from this evidence.
- `EPOCH_ADVANCE_NO_OBSERVED_GAIN` — epoch advanced but the recorded balance did not increase.
- `EPOCH_REGRESSION` — a later observation reports a lower epoch.
- `BALANCE_REGRESSION` — a later observation reports a lower balance.
- `SAME_EPOCH_BALANCE_CONFLICT` — balance changed while the reported epoch did not.

These are observation diagnostics, not protocol-fault conclusions. For example, a balance change can have causes outside mining rewards, and an observation gap can hide intermediate events.

## Verification

The JSON artifact binds:

1. canonical source evidence SHA-256;
2. canonical report SHA-256;
3. rendered Markdown SHA-256.

Verification fully recompiles from the source evidence rather than merely checking stored hashes:

```bash
python reward_reconciliation.py verify examples/reward-observations.example.json reward-report.json
```

A verified artifact prints `{"ok": true}` and exits `0`. Tamper, schema drift, source rebinding, or recomputation mismatch exits nonzero.

## Fail-closed boundaries

The compiler rejects non-regular, multiply linked, symlinked/reparse, unstable, oversized, or non-UTF-8 JSON trust roots; duplicate JSON keys; unknown fields; bool-as-number tricks; negative/nonfinite balances; negative/non-integer epochs; malformed miner IDs; duplicate or non-increasing observation times; unsupported timestamp offsets; and reports that do not exactly recompile.

Every report carries explicit false authority for payout owed, expected reward inference, node/wallet/payout mutation, bounty submission, RTC transfer, payment acceptance, and revenue recognition.
