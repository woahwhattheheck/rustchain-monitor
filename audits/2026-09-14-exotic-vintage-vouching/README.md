# RustChain exotic vintage-tier vouching audit (2026-09-14)

## Status

**SOURCE RED on upstream `Scottcjn/Rustchain@aa584b344a766f6c0f8613ba7198d1cc7ffbae35`.** This is an offline, non-production reproduction of a reward-integrity defect in `node/rustchain_v2_integrated_v2.2.1_rip200.py::_detect_exotic_arch()` / `derive_verified_device()`.

This carrier exists so the finding, reproduction, and remediation design are durable and reviewable without modifying or probing the production network.

## Finding

Merged upstream PR #8106 fixed the generic claimed-tier echo for ARM/console claims by neutral-capping unvouched bonus claims, but its own disclosure explicitly left the adjacent exotic-family tiers untouched and still claimable.

On the current upstream main authority above, `_detect_exotic_arch(device)` runs before the generic vouch/cap tail and accepts several client-controlled claim fields as sufficient evidence. Two concrete examples are:

- `family="sparc", arch="sparc_v7"` -> `SPARC/sparc_v7`
- `family="m68k", arch="68000"` -> `M68K/68000`

Those early returns bypass the neutral cap that #8106 added for unvouched premium claims. A modern host can therefore select an antiquity-weighted identity by changing attestation metadata rather than proving the physical architecture.

The issue is broader than deleting the `family_lower == ...` terms: `machine` and CPU-brand/model fields are also client-supplied in this path. The repository's own `CPU_ANTIQUITY_SYSTEM.md` describes exotic validation as a multi-signal decision involving machine, CPU brand, SIMD evidence, and cache topology; the live detector does not require that corroboration before returning a premium exotic identity.

## Impact

RustChain's epoch reward pot is apportioned by hardware weight. A forged vintage/exotic multiplier therefore increases the attacker's share at the expense of honest miners. This report does **not** claim extra RTC is minted and does not exercise the defect against production.

The defect is reward integrity / significant business-logic impact: a client-controlled metadata claim can cross a trust boundary into a higher payout tier before the generic unvouched-device cap executes.

## Safe reproduction

Run `repro_exotic_reward_spoof.py` against a local checkout of the pinned upstream source:

```bash
python3 audits/2026-09-14-exotic-vintage-vouching/repro_exotic_reward_spoof.py \
  /path/to/Rustchain/node/rustchain_v2_integrated_v2.2.1_rip200.py
```

The reproducer parses and executes only the small detector/helper definitions from the local source file. It makes no network calls, writes no chain state, and does not import/start the node.

Expected vulnerable behavior includes a modern x86-shaped device receiving a server-derived SPARC or M68K identity solely because the request claims that family/arch.

## Collision / provenance

Before publication, exact GitHub searches for `_detect_exotic_arch`, `sparc_v7`, and the SPARC/m68k spoof class found only the already-merged #8106 disclosure; a Slack collision search for the same class returned no newer claim. A broad older design issue (#6750) discusses fingerprint forgery generally, but #8106 explicitly identifies this concrete adjacent implementation defect as untouched/still claimable.

## Recommended remediation

See `REMEDIATION.md`. The emergency invariant is simple: **an unvouched premium architecture claim may lose its bonus, but must not lose its ability to participate.** Until an exotic family has architecture-specific positive corroboration, map it to the existing neutral `UNVOUCHED_DEVICE` / 0.8 path rather than trusting claim metadata or zeroing the miner.

## Authority / references

- Upstream source authority: `Scottcjn/Rustchain@aa584b344a766f6c0f8613ba7198d1cc7ffbae35`
- Prior hardening/disclosure: `Scottcjn/Rustchain#8106` (merged as `95deda64001de348222be198b28f2cd29b33f0b4`)
- Ongoing sponsor program: `Scottcjn/rustchain-bounties#71`

No production exploitation was performed.