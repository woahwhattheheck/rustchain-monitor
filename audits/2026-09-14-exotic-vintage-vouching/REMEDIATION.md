# Remediation design: exotic reward-tier vouching

## Security invariant

A client may **claim** any architecture, but a claim that raises reward weight above the neutral modern rate must not become a paid identity until the server has architecture-specific positive corroboration.

If corroboration is unavailable or inconclusive, preserve participation and map the miner to the existing neutral `UNVOUCHED_DEVICE` path. Do not zero the miner merely because old hardware cannot produce a modern measurement.

## Why a one-line denylist is insufficient

The current `_detect_exotic_arch(device)` trusts several request-controlled surfaces:

- claimed family / arch;
- `machine` / platform-machine label;
- CPU/model/brand strings.

Removing only `family_lower == "sparc"` or `family_lower in ("m68k", ...)` leaves equivalent spoof paths through the other client strings. Likewise, checking that two client strings agree is not independent corroboration; an attacker controls both.

The repository documentation already describes a stronger intended evidence set: machine field, CPU brand, SIMD evidence, and cache topology. The paid decision should therefore happen after validated fingerprint evidence is available, not in a claim-only early-return helper.

## Emergency hardening

The least risky immediate mitigation is fail-neutral for premium exotic claims:

1. Keep `_detect_exotic_arch()` useful as a *candidate classifier*, but do not let its result directly authorize a premium reward tier.
2. In `derive_verified_device()`, pass the candidate plus the validated fingerprint to an architecture-specific vouching function.
3. If no verifier exists for that family, or if its required evidence is missing/contradictory, return `UNVOUCHED_DEVICE` (modern neutral rate) rather than the premium candidate.
4. Preserve existing explicitly vouched paths (for example current PowerPC/x86/console logic) and add exotic families incrementally as positive evidence contracts become testable.

Pseudo-shape:

```python
candidate = _detect_exotic_arch(device)
if candidate:
    if _vouch_exotic_candidate(candidate, device, fingerprint):
        return candidate
    return dict(UNVOUCHED_DEVICE)
```

The key requirement is that `_vouch_exotic_candidate` must not merely compare multiple attacker-controlled labels.

## Architecture-specific evidence

A production verifier should consume measurements that are already validated/recomputed by server-side code where possible. Candidate examples, subject to fleet testing:

- SPARC: SPARC-specific SIMD/instruction characteristics plus cache/timing profile consistent with the claimed generation; reject x86/ARM SIMD contradictions.
- M68K: absence/presence constraints appropriate to the exact 68K generation plus vintage-native timing/feature evidence; reject modern x86/ARM SIMD/platform contradictions.
- MIPS/RISC-V/IA-64/S390/SuperH: define explicit evidence contracts before granting an above-neutral tier.

If the current client cannot supply enough evidence for a family, that is a client/server migration problem. The safe interim state is neutral reward, not trusting a string and not banning the miner.

## Regression tests

At minimum add tests proving:

- modern x86 + `family=sparc, arch=sparc_v7` cannot earn a premium identity;
- modern x86 + `family=m68k, arch=68000` cannot earn a premium identity;
- changing only `machine` or CPU-brand labels cannot independently unlock a premium exotic tier;
- contradictory modern SIMD/cache evidence forces neutral classification;
- an unvouched claim remains eligible at the neutral rate rather than being zeroed;
- genuine exotic fixtures that meet a defined verifier contract retain their intended tier.

Tests should cover both enrolment weighting and settlement antiquity lookup, since RustChain has historically had two reward tables/paths.

## Rollout

1. Land neutral-cap regression tests first.
2. Land the emergency gate with no production probing.
3. Observe whether any legitimate exotic fleet members are neutral-capped; use their existing attestation payloads to design family-specific positive evidence.
4. Restore premium tiers family-by-family only after those evidence contracts have red/green tests.

This mirrors the safety principle used by merged PR #8106: losing an unproven bonus is acceptable; losing the ability to mine is not.