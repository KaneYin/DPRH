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

## V1 (PREFETCH flag reaches MemCtrl) — AWAITING CLUSTER
- Procedure + temp-probe: tests/dprh/test_v1_prefetch_flag.md
- Code-path analysis (macOS): Cache::createMissPacket (cache.cc:553) reuses the
  same req, so the PREFETCH flag is EXPECTED to survive to the MC without a
  forwarding patch. Confirm empirically on cluster (HANDOFF C6).
- No forwarding patch committed (none expected; apply only if cluster count==0).

## Default-off invariant (Task 6 Step 6) — by construction; verify on cluster
- New MemCtrl params enable_dprh/demand_first/enable_filter all default False.
- chooseNext frfcfs branch: DPRH code runs only under `if (enableDprh)`, and the
  Phase 0 dprhChooseNext returns queue.end() (no-op) even when true. So an
  unflagged/B1 build reproduces stock FR-FCFS byte-for-byte by construction.
- Cluster verification: HANDOFF C7 (diff b1_smoke vs b1_after6 -> no diff).

## Phase 1 stats framework (Task 9) — authored; presence check is [cluster] C11
- Populated in Phase 0 (computed with existing packetReady/burstReady only, no
  parallel timing model, no DRAMInterface edits):
    schedCycles, cyclesNoLegalDemand, cyclesHslot, readyRowHitPrefetch,
    nonHslotReason[DEMAND_READY|NO_PREFETCH], demandReadLatency,
    prefetchReadLatency, readWriteTurnarounds.
- Declared but populated in Phase 1/3 (predicate refinement; kept at 0 for now,
  hidden by nozero where applicable): turnaroundUnsafe, agedDemandBlocked,
  demandRowHits, prefetchRowHits, nonHslotReason[PF_NOT_ROWHIT|TURNAROUND_UNSAFE
  |AGED_DEMAND]. Row-hit split is deferred because it would require reading DRAM
  bank open-row state (DRAMInterface is a hard-invariant no-touch); it is
  computed in Phase 1 via a permitted probe, not by editing timing code.
- H_slot predicate lives in single helper MemCtrl::hasLegalDemand (unit-testable
  in Phase 1).
- Cluster verification: HANDOFF C11 (all six grepped stats -> OK).

## Gate status
- G0: NOT EVALUATED

## Run snapshots
(populated as tasks complete)

### Task 2 — gem5.opt build (AWAITING CLUSTER)
- Build wrapper authored: scripts/build_gem5.sh
- Cluster must run: ./scripts/build_gem5.sh 2>&1 | tee results/build.log
- Then fill "built on <cluster hostname>" above and record build.log tail here.

### Task 4 — B1 emits SPP prefetches (V1 precondition) — AWAITING CLUSTER
- Prefetcher wiring lives in gem5/configs/dprh/run_se.py:attach_private_caches
  (SPP attached to L2 as `l2cache.prefetcher`, prefetch_on_access=True).
- Cluster must run HANDOFF C4 (B1 smoke) then C5 (grep prefetch-issued stat).
- Record the exact L2 prefetch-issued stat name here after the run
  (gem5-version-specific; candidate: system.cpu.l2cache.prefetcher.pfIssued).
- If zero: fix wiring in run_se.py before continuing (V1 needs prefetches).
