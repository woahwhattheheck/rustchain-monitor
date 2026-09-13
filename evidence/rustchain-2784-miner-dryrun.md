# RustChain bounty #2784 — Linux miner dry-run hardware report

Claimant / miner_id: `woahwhattheheck`

Bounty: https://github.com/Scottcjn/rustchain-bounties/issues/2784

## Source and command

- RustChain source: `Scottcjn/Rustchain@8c79fba7561283ff8c880258152cd15e2610c312` (fresh upstream `main` at test time)
- Miner: `miners/linux/rustchain_linux_miner.py` v2.2.1-rip200
- Command:

```bash
python3 miners/linux/rustchain_linux_miner.py \
  --dry-run \
  --wallet woahwhattheheck \
  --verbose \
  --show-payload
```

The exact source commit was checked out by the evidence workflow with persisted checkout credentials disabled.

## Test environment

- GitHub-hosted runner image: Ubuntu 24.04.5 LTS (`ubuntu-24.04`)
- Kernel: Linux 6.17.0-1022-azure
- Architecture: x86_64
- CPU: AMD EPYC 9V74 80-Core Processor
- Exposed CPUs: 4 vCPUs
- RAM: 15 GiB
- Hypervisor: Microsoft, full virtualization
- `systemd-detect-virt`: `microsoft`
- CPU flags include `hypervisor`

## Fingerprint results

The current miner ran all six fingerprint checks:

| Check | Result |
|---|---|
| Clock-skew / oscillator drift | PASS |
| Cache timing fingerprint | PASS |
| SIMD unit identity | PASS |
| Thermal drift entropy | PASS |
| Instruction path jitter | PASS |
| Anti-emulation | **FAIL** |

Overall fingerprint status: **FAILED / False**.

This is the expected and useful outcome for this hosted VM: the first five timing/CPU checks passed, while anti-emulation correctly identified that the environment is virtualized.

## Other observed behavior

- Dry-run explicitly printed: `No mining or network state will be modified`.
- The miner used an **ephemeral keypair** and printed that it was not saving `miner_key.json`.
- Hardware probing reported 2 MAC addresses and `Serial present: yes`. A serial being available inside a hosted VM is mildly surprising, but anti-emulation still correctly rejected the environment.
- The optional read-only health probe returned **HTTP 200** from `https://rustchain.org/health`.
- Node version reported by the probe: **2.2.1-rip200**.
- The dry-run process exited successfully; no attestation, enrollment, or production mining was attempted.

## Reproducible evidence

- Successful workflow run: https://github.com/woahwhattheheck/rustchain-monitor/actions/runs/34786965667
- Evidence workflow commit: https://github.com/woahwhattheheck/rustchain-monitor/commit/ce36d27ba70bf9829b88ff93103c731b5676b8fe
- Uploaded raw dry-run log artifact: https://github.com/woahwhattheheck/rustchain-monitor/actions/runs/34786965667/artifacts/10326592744
- Artifact ID: `10326592744`
- Artifact ZIP SHA-256 reported by GitHub Actions: `24992f4908d03cbf9702774c2b3a7fd3e30d6708b4ae6dacf4bf186ca16bc0d9`

The GitHub Actions job concluded `success`; the exact miner dry-run step and artifact upload both concluded `success`.
