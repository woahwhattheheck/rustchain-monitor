# Reward exception casebook

`reward_casebook.py` turns one or more **verified** `reward_reconciliation.py` artifacts into a deterministic operator work queue.

It solves a different problem from reconciliation. Reconciliation answers: **what did the observations show?** The casebook answers: **which non-routine transitions still need a human decision, what exact evidence produced each case, and what disposition has an operator explicitly recorded?**

The casebook is intentionally not a payout engine, expected-reward model, bounty submission surface, or customer/provider workflow.

## Trust model

A casebook manifest names original observation sources and the reconciliation artifacts compiled from them. Before any case is emitted, `reward_casebook.py` calls `verify_reconciliation(source, artifact)`. That verifier recompiles the full reconciliation from the source and requires byte-for-byte logical equality across the report, Markdown, and receipt.

A reconciliation with a changed transition, summary, receipt digest, Markdown body, miner identity, or source observation therefore cannot be promoted into the operator queue.

Each accepted evidence set contributes its source, report, and Markdown SHA-256 identities. Material cases derive a stable `rcase-<sha256>` identifier from:

- miner identity;
- source SHA-256;
- report SHA-256;
- from/to observation timestamps;
- from/to epochs;
- severity; and
- signal list.

Operator state and the reporting `as_of` timestamp are deliberately excluded from that identifier. A case remains the same case when it is later acknowledged or resolved.

## Materiality boundary

Only reconciliation transitions with severity `CAUTION` or `ANOMALY` become cases. `INFO` transitions remain present in the underlying verified reconciliation but do not create operator queue entries.

Examples of material signals already produced by reconciliation include:

- `EPOCH_REGRESSION`;
- `BALANCE_REGRESSION`;
- `SAME_EPOCH_BALANCE_CONFLICT`;
- `OBSERVATION_GAP`; and
- `EPOCH_ADVANCE_NO_OBSERVED_GAIN`.

A casebook whose verified evidence contains only `INFO` transitions is valid and has zero cases. Zero cases means **no material transition was present in the supplied evidence under the current reconciliation classifier**. It does not mean a miner was paid correctly or that revenue was earned.

## Operator dispositions

Cases begin as `OPEN`. The manifest may explicitly record one of two later states:

- `ACKNOWLEDGED`: a human has taken custody of the case, but the underlying evidence is not represented as resolved;
- `RESOLVED`: a human explicitly records the case as resolved.

A disposition contains the stable case ID, status, UTC `updated_at`, and an operator note. The compiler rejects:

- dispositions for unknown case IDs;
- duplicate dispositions for one case;
- disposition timestamps later than the manifest `as_of` time;
- disposition timestamps earlier than the case observation; and
- notes longer than 2,000 characters.

There is no automatic resolution path. The output authority fence fixes `automatic_resolution` to `false`.

## Manifest

Schema: `rustchain.reward-casebook-manifest/v1`

```json
{
  "schema_version": "rustchain.reward-casebook-manifest/v1",
  "as_of": "2026-09-13T10:00:00Z",
  "entries": [
    {
      "source": "reward-observations.json",
      "artifact": "reward-reconciliation.json"
    }
  ],
  "dispositions": []
}
```

All entry paths are relative to the manifest directory. Absolute paths, `..` traversal, symlink file inputs, duplicate entries, missing files, and more than 1,000 evidence entries fail closed.

The `as_of` field is explicit rather than reading the wall clock. This keeps case ages and receipts reproducible.

## Compile and verify

First compile reconciliation evidence using the existing surface:

```bash
python reward_reconciliation.py compile \
  reward-observations.json \
  --json-out reward-reconciliation.json \
  --markdown-out reward-reconciliation.md
```

Then compile the casebook:

```bash
python reward_casebook.py compile \
  reward-casebook-manifest.json \
  --json-out reward-casebook.json \
  --markdown-out reward-casebook.md
```

Verify it later against the same manifest and evidence:

```bash
python reward_casebook.py verify \
  reward-casebook-manifest.json \
  reward-casebook.json
```

Successful verification prints:

```json
{"ok": true}
```

Compilation refuses to overwrite an existing JSON or Markdown output.

## Output

The JSON artifact has three top-level members:

- `casebook`: material cases, summary counts, evidence-bound IDs, explicit operator state, ages, and the authority fence;
- `markdown`: deterministic operator-readable rendering of the same casebook;
- `receipt`: SHA-256 of the casebook JSON value, Markdown, and normalized input identity.

The input identity binds:

- the explicit `as_of` timestamp;
- every verified evidence set's miner/source/report/Markdown identity; and
- normalized dispositions sorted by case ID.

Recompiling with the same evidence, dispositions, and `as_of` value produces the same artifact. Moving `as_of` forward intentionally changes ages and the input receipt while preserving stable case IDs.

## Authority boundary

Every compiled casebook states all of these as `false`:

- `payout_owed`;
- `expected_reward_inferred`;
- `node_mutation`;
- `wallet_mutation`;
- `payout_mutation`;
- `provider_contact`;
- `external_submission`;
- `payment_acceptance`;
- `revenue_recognition`; and
- `automatic_resolution`.

A negative observed balance delta is evidence of a balance regression in the supplied observation series. It is **not**, by itself, proof that a payout is missing, a provider owes RTC, a bounty should be filed, or revenue should be recognized.

## Operational pattern

A useful fleet loop is:

1. export or compile a frozen observation source;
2. compile and verify its reconciliation artifact;
3. add the source/artifact pair to the casebook manifest;
4. compile the casebook at an explicit `as_of` time;
5. work `ANOMALY` cases before `CAUTION` cases;
6. add an explicit disposition only after a human review; and
7. recompile/verify to obtain a new receipt.

The original observation and reconciliation artifacts remain immutable throughout the process. The casebook is a triage layer over evidence, not a place to rewrite the evidence.
