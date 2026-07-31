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
    schedCycles, cyclesNoLegalDemand, cyclesHslot, cyclesReadyPrefetchNoDemand,
    cyclesHslotUpperGap, nonHslotReason[DEMAND_READY|NO_PREFETCH|PF_NOT_ROWHIT
    |TURNAROUND_UNSAFE], turnaroundUnsafe, demandReadLatency,
    prefetchReadLatency, readWriteTurnarounds.
    [SUPERSEDED BY FIX-1: `cyclesHslot` originally counted the proxy upper bound
    (no row-hit/turnaround check); the row-hit split below was WRONGLY deferred.
    See the FIX-1 section for the corrected predicate and stats. `cyclesHslot`
    is now the true H_slot; the proxy lives in `cyclesReadyPrefetchNoDemand`.]
- Declared but populated in Phase 1/3 (predicate refinement; kept at 0 for now,
  hidden by nozero where applicable): agedDemandBlocked,
  demandRowHits, prefetchRowHits, nonHslotReason[AGED_DEMAND]. [FIX-1: the
  row-hit-dependent items are no longer deferred — reading DRAM open-row state
  READ-ONLY does not touch the timing-logic invariant; see FIX-1.]
- H_slot predicate: the demand side is in MemCtrl::hasLegalDemand; the full
  cycle verdict (prefetch/row-hit/turnaround side) is the pure, unit-tested
  dprh::classifyHslotCycle in mem/dprh_hslot.hh (added by FIX-1).
- Cluster verification: HANDOFF C11 (all six grepped stats -> OK).

## FIX-1 (CRITICAL) — cyclesHslot did not measure H_slot

### Formal predicate (research_plan.md §5, line 129)
H_slot(cycle) = (no timing-legal demand command this cycle)
              AND (∃ accepted prefetch p:
                     timing-ready(p) AND row-hit(p) AND turnaround-safe(p)).

### Root-cause evidence (source, not the doc)
Population site: `MemCtrl::chooseNext`, frfcfs branch, mem_ctrl.cc:617-643.
Conjunct → code location → present/absent (pre-fix):

| conjunct                    | code                                    | status |
|-----------------------------|-----------------------------------------|--------|
| no timing-legal demand      | `!hasLegalDemand(queue,mem_intr)` :622   | PRESENT |
| ∃ prefetch p                | `mp->pkt->req->isPrefetch()` :631        | PRESENT |
| timing-ready(p)             | `packetReady(mp, mem_intr)` :632         | PRESENT |
| **row-hit(p)**              | *none* — loop 627-636 ignores open row   | **ABSENT (bug)** |
| **turnaround-safe(p)**      | *none* — no bus-direction test           | **ABSENT (bug)** |

The two absent conjuncts are the bug: `++stats.cyclesHslot` (mem_ctrl.cc:639)
fired on *any* timing-ready accepted prefetch, i.e. an upper bound on true
H_slot that admits row-conflict prefetches (which cost precharge+activate) and,
in principle, turnaround-forcing prefetches. The stat literally named
`readyRowHitPrefetch` was incremented in the same block without any row-hit
check — a misnomer confirming the gap.

### Refuting the deferral rationale (this log, lines 38-43)
The prior note deferred the row-hit split claiming "DRAMInterface is a
hard-invariant no-touch." That invariant forbids modifying *timing logic*. The
open-row state is read read-only in the existing FR-FCFS ranker at
dram_interface.cc:92,108 as `const Bank& bank = ranks[pkt->rank]->banks[pkt->bank];
... bank.openRow == pkt->row;`. A **const accessor** over that same existing
state performs no timing computation and mutates nothing, so it does not touch
the invariant. Row-hit is therefore obtainable read-only in Phase 0; the
deferral was over-conservative. Discrepancy logged; source wins.

### Turnaround-safety finding
The H_slot accounting runs only inside the `busState == READ` path
(mem_ctrl.cc:1055) and all read-queue prefetches are reads, so a harvested
prefetch never forces a bus turn *at this site* — turnaround-safe is effectively
always true here, and the `TURNAROUND_UNSAFE` bin will read ~0 in the read path.
The conjunct is still wired in for definitional correctness and unit-testability
(the pure predicate is exercised with a mismatched-direction case).

### Fix (applied — see commit dprh(phase0-fix): FIX-1)
- Added read-only `MemInterface::isRowHit` (default false) +
  `DRAMInterface::isRowHit` override (const, reads `banks[].openRow`; no timing
  edit). Reuses the exact state the FR-FCFS ranker already inspects.
- Extracted the pure cycle classifier into `mem/dprh_hslot.hh`
  (`dprh::classifyHslotCycle`) so the predicate is unit-testable without a full
  MemCtrl. `chooseNext` now does one pass computing {anyReadyPrefetch,
  anyReadyRowHit, anyHarvestable} and defers the verdict to it.
- Stats: `cyclesHslot` keeps the TRUE predicate; misnamed `readyRowHitPrefetch`
  renamed to `cyclesReadyPrefetchNoDemand` (the proxy/upper bound, a
  decomposition term); added `cyclesHslotUpperGap = proxy − true`. Decomposition
  bins `PF_NOT_ROWHIT` / `TURNAROUND_UNSAFE` and `turnaroundUnsafe` now populated.
- Test: `mem/dprh_hslot.test.cc` covers the plan's three cases
  (row-conflict-only → proxy only; row-hit same-dir → both; row-hit needing a
  turn → proxy only, TURNAROUND_UNSAFE bin).
- Invariants held: no DRAM timing logic touched, no write-drain change, all DPRH
  flags still default off.

## SPEC bring-up + MPKI profiling (Task 11) — AWAITING CLUSTER (data-dependent)
- SE-mode cross-compile: benchmarks/README.md (fill exclusion list on cluster).
- Profiler: scripts/mpki_profile.py (runs run_se.py --config B1 --prefetcher spp,
  computes LLC_MPKI = system.llc.demandMisses::total / (simInsts/1000)).
- Cluster: HANDOFF C14 (cross-compile) then C15 (profiling pass).
- FROZEN SUITES (fill from results/mpki_profile.csv on cluster; DO NOT invent):
    R2 main set (MPKI >= 1):        __________
    R3 no-harm controls (MPKI<0.5): __________
    R5 held-out (2-3, from R2, excluded from all tuning): __________
  These MUST be filled from measured MPKI on the cluster before Phase 1.

## 3-trace smoke test (Task 12) — AUTHORED; AWAITING CLUSTER EXECUTION
- Driver: scripts/smoke_test.sh (B0/B1/B2 x 3 memory-intensive benches ->
  results/smoke.csv with IPC, LLC MPKI, DRAM row-hit rate).
- Cluster: HANDOFF C16. Pick 3 memory-intensive benches from the Task 11 R2 set.
- Sanity relations to verify FROM MEASURED smoke.csv (do NOT pre-judge):
    (a) B1 IPC > B0 IPC on memory-intensive traces;
    (b) B2 differs measurably from B1 on >=1 trace (row-hit rate / latency);
    (c) no NaN/zero IPC.
  Record the three numbers per trace here after the cluster run.

## Task 13 (Option B feedback wiring) — CONDITIONAL SKIP (decision deferred to cluster)
- Task 13 fires ONLY if the cluster finds Tasks 9 & 12 not green. Its purpose is
  G0 interop, not completing Option B.
- Phase 0 default: SKIPPED (documented). The noteUseful/noteEvicted feedback is
  intentionally stubbed (optimistic accuracy) per plan/refs/option_b_limitations.md,
  which is acceptable because Phase 1 instruments B1/B2 (filter accepts) and does
  not depend on drop behavior.
- Cluster action: after HANDOFF C11 (Task 9 stats) and C16 (Task 12 smoke), if
  both are green, leave Task 13 SKIPPED. If not, wire noteUseful/noteEvicted from
  the LLC prefetch used/evicted signals (hook points in option_b_limitations.md),
  re-run HANDOFF C9, and commit "dprh(phase0): wire Option B filter feedback".

## Gate status
- G0: NOT EVALUATED — AWAITING CLUSTER (four conditions below are data-dependent)

### Gate G0 evaluation scaffold (Task 14) — fill from cluster data; DO NOT pre-sign
Each condition records: evidence source, measured value, PASS/FAIL. All four are
cluster-measured; none may be marked PASS without observed cluster output.

- G0.a — B1 vs B0 prefetch speedup on memory-intensive traces
    Source: results/smoke.csv (B1 IPC > B0 IPC on the memory-intensive traces).
    Measured: __________    Verdict: AWAITING CLUSTER

- G0.b — B2 vs B1 differs measurably on >=1 trace
    Source: results/smoke.csv (row-hit rate / mean latency differs B1 vs B2).
    Measured: __________    Verdict: AWAITING CLUSTER

- G0.c — V1 passes (PREFETCH flag reaches MemCtrl)
    Source: tests/dprh/test_v1_prefetch_flag.md (v1PrefetchReadsSeen > 0) or the
    permanent prefetchReadLatency::samples > 0 (Task 9/10).
    Measured: __________    Verdict: AWAITING CLUSTER

- G0.d — traffic-gen calibration passes (row-hit monotonic in num_seq_pkts)
    Source: scripts/analyze_calibration.py output over cal_1/cal_4/cal_16.
    Measured: __________    Verdict: AWAITING CLUSTER

### Sign-off (only when ALL four PASS on cluster)
- If all PASS: set "G0: PASSED (<date>)" with the four evidence lines, the frozen
  commit (51edbbb9cfd37e92e9901aea2caa4a8f20eda005), and D-A0/D-A0b confirmations,
  then commit "dprh(phase0): Gate G0 evaluated — PASS".
- DO NOT start Phase 1 until G0 is PASSED. If any condition fails, the failing
  task's fix is the next work item.

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
