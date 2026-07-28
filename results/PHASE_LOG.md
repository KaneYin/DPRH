# DPRH Phase Log

## Frozen environment (Phase 0)
- gem5 base commit: 51edbbb9cfd37e92e9901aea2caa4a8f20eda005
- gem5 describe: v25.1.0.1-1-g51edbbb9cf
- gem5 version: 25.1.0.1
- Build: gem5.opt, x86 ISA, built on <cluster hostname>  [AWAITING CLUSTER — Task 2]
- DRAM device: DDR4_2400_16x4 (see D-A0b)  [x] confirmed by user (locked to plan default)
- Primary prefetcher: SignaturePathPrefetcher (see D-A0)  [x] confirmed by user (locked to plan default)
- Address mapping: RoRaBaCoCh | Page policy: open_adaptive | Read buffer: 64

## Divergences from MSF (Chapter 5) — logged, intentional
- Single-core (DPRH) vs 8-core (MSF)
- LLC 2 MB/16-way (DPRH) vs 16 MB/8-way shared (MSF)
- L2 8-way (DPRH §3.2) vs 4-way (MSF)
- DDR4_2400_16x4 (DPRH) vs DDR4-3200 (MSF)  [ ] revisit if D-A0b flips

## Gate status
- G0: NOT EVALUATED

## Run snapshots
(populated as tasks complete)

### Task 2 — gem5.opt build (AWAITING CLUSTER)
- Build wrapper authored: scripts/build_gem5.sh
- Cluster must run: ./scripts/build_gem5.sh 2>&1 | tee results/build.log
- Then fill "built on <cluster hostname>" above and record build.log tail here.

### Prefetcher-issued stat name (Task 4 Step 2) — TBD on cluster
- The exact L2 prefetch-issued stat name is gem5-version-specific; record here
  after the B1 smoke run (candidate: system.cpu.l2cache.prefetcher.pfIssued).
