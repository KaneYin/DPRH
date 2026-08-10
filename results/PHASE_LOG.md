# DPRH Phase Log

## Frozen environment (Phase 0)
- gem5 base commit: 51edbbb9cfd37e92e9901aea2caa4a8f20eda005
- gem5 describe: v25.1.0.1-1-g51edbbb9cf
- gem5 version: 25.1.0.1
- Build: gem5.opt, x86 ISA, built on <cluster hostname>  [AWAITING CLUSTER — Task 2]
- DRAM device: DDR4_2400_16x4 (see D-A0b)  [x] confirmed by user (locked to plan default)
- Primary prefetcher: SignaturePathPrefetcher (see D-A0)  [x] confirmed by user (locked to plan default)
- Address mapping: RoRaBaCoCh | Page policy: open_adaptive | Read buffer: 64

## FIX-6 — Frozen system parameters (H_slot-relevant; single source: dprh_common.FROZEN)
Row-hit availability -- hence H_slot itself -- depends on page policy, address
mapping, and read-queue depth, so these are frozen in `dprh_common.py:FROZEN`
(the single source used by make_mem_ctrl) and printed by every run as a
`[dprh-frozen] ...` line (frozen_summary(); see run_se.py / run_trafficgen.py).

| Parameter              | Frozen value                         |
|------------------------|--------------------------------------|
| DRAM device            | DDR4_2400_16x4                       |
| Address mapping        | RoRaBaCoCh                           |
| Page policy            | open_adaptive                        |
| Mem sched policy       | frfcfs                               |
| Read buffer size       | 64                                   |
| Write buffer size      | 64                                   |
| Channels               | 1 (single MemCtrl/DRAM interface)    |
| Ranks per channel      | 2  (DDR4_2400_16x4 default)          |
| Banks per rank         | 16 (DDR4_2400_16x4 default)          |
| Clock                  | 4GHz                                 |
| Primary prefetcher     | SignaturePathPrefetcher (SPP)        |
| Sensitivity prefetcher | StridePrefetcher                     |
| gem5 base (frozen)     | v25.1.0.1-11-g9a6e31ed2a (research/dprh) |

Notes:
- Prefetcher assignment: SPP is PRIMARY, Stride is the SENSITIVITY prefetcher.
  This corrects the plan's original Stride/SPP wording (D-A0); the code already
  reflected it (make_prefetcher), now recorded here.
- gem5 base drift: the frozen base advanced from the original
  51edbbb9cf (v25.1.0.1-1) to 9a6e31ed2a (v25.1.0.1-11, an upstream gem5:stable
  merge) during the FIX-1 rebase onto origin/research/dprh. The "Frozen
  environment" block above records the pre-rebase base; this row is the current,
  authoritative one. DRAM timing was not touched (hard invariant).
- Self-documentation: every simout now carries a `[dprh-frozen]` line; a
  results-aggregation step can refuse to merge runs whose line disagrees.

## FIX-5 (LOW) — guard the positional createDram tag_prefetch argument
### Root cause (evidence in source)
run_trafficgen.py passes tag_prefetch POSITIONALLY as the 15th arg to
PyTrafficGen.createDram, because PyBindMethod (PyTrafficGen.py:60) binds the raw
C++ method and does not surface C++ default args to Python. createDram's C++
signature (base.cc:412-421) ends in `bool tag_prefetch`. If an upstream gem5
upgrade inserts a parameter, our positional bool silently binds to the wrong
slot and tag_prefetch takes its C++ default (false) -- no error, corrupted R6
experiments (prefetch stream no longer tagged).
### Readback investigation
Nothing on the Python side is readable: the createDram return is an opaque
BaseGen handle, and PyTrafficGen exposes no tag param. Per the plan's fallback,
added a trivial const getter.
### Fix (applied)
- base.hh: BaseTrafficGen gains `bool lastDramTagPrefetch` (protected) +
  `bool getLastDramTagPrefetch() const`; base.cc: createDram sets
  lastDramTagPrefetch = tag_prefetch before constructing the DramGen;
  PyTrafficGen.py: export PyBindMethod("getLastDramTagPrefetch").
- run_trafficgen.py: both generators now capture the createDram return, assert
  `getLastDramTagPrefetch() == <intended>` (demand: False; prefetch:
  bool(--pf-tag)), then yield; the tag line carries the required
  "POSITIONAL ARG -- verify slot on any gem5 upgrade (see FIX-5)" comment.
  A slot shift makes the actual bound value differ from intended -> assertion
  fires before any data is produced.
- Test/acceptance: assertion logic demonstrated locally (mock) -- passes on a
  correct bind, fires on a permuted/wrong-slot bind. Full behavior is
  cluster-verified (needs gem5); compile is verified by CI (build.yml).
- Invariants untouched (no DRAM timing, no write-drain, flags default off; the
  getter is read-only and stock createDram callers use the C++ default).

## Divergences from MSF (Chapter 5) — logged, intentional
- Single-core (DPRH) vs 8-core (MSF)
- LLC 2 MB/16-way (DPRH) vs 16 MB/8-way shared (MSF)
- L2 8-way (DPRH §3.2) vs 4-way (MSF)
- DDR4_2400_16x4 (DPRH) vs DDR4-3200 (MSF)  [ ] revisit if D-A0b flips

## V1 (PREFETCH flag reaches MemCtrl) — FAILED; REPAIR AWAITING CLUSTER
- Procedure: HANDOFF C2·V1 (front gate). [FIX-3: moved to first data-producing
  slot; permanent prefetchEnqueued counter replaces the removable temp probe.]
- Cluster result (2026-08-09, gem5 `1aa651d01a`): L2 SPP `pfIssued = 499`,
  `system.mem_ctrl.prefetchEnqueued = 0` -- **FAIL**. C3+ remains blocked.
- Corrected root cause: `Queued::DeferredPacket::createPkt` constructed the
  hardware-prefetch `Request` with flags `0`. `HardPFReq` identified it only in
  the initial packet; `Cache::createMissPacket` later replaced that command with
  a normal read while reusing the still-unmarked Request. The earlier analysis
  correctly observed RequestPtr reuse but incorrectly assumed the source
  Request was already marked.
- Repair authored: add default-false
  `QueuedPrefetcher.mark_request_as_prefetch`; set `Request::PREFETCH` at request
  creation when enabled; enable it only in DPRH SPP/Stride profiles. This keeps
  stock queued-prefetcher behavior unchanged. Rebuild and rerun HANDOFF C2·V1.

## FIX-3 (HIGH) — front-load V1 with a permanent prefetch-enqueue counter
### Root cause / risk (evidence in source)
V1 (does the PREFETCH flag reach the MC?) sat at C6, behind B1/B2 work and behind
a TEMP probe that had to be re-applied + rebuilt to run. If the flag were lost,
the Option B filter (mem_ctrl.cc:219, gated on pkt->req->isPrefetch()) and every
prefetch-classified stat are dead code, and C7-C16 produce plausible garbage.
The original survival argument was incomplete: cache.cc:553 reuses the
originating request, but the queued hardware-prefetch source constructed that
request with flags `0`. The 2026-08-09 cluster run exposed this source-side gap.
### Fix (applied)
- Added permanent counter `prefetchEnqueued` incremented at the MC read-queue
  enqueue site (addToReadQueue, before the Option B filter so it is independent
  of filter config). This is the runtime V1 assertion; no temp probe needed.
- Reordered CLUSTER_HANDOFF.md: new HARD GATE C2·V1 is the first data-producing
  run after build. Its verdict is self-contained (also greps prefetch-issued,
  folding in old C5): pfIssued==0 => wiring bug; pfIssued>0 & prefetchEnqueued==0
  => flag dropped en route (STOP, patch forwarding); both>0 => PASS. The handoff
  forbids proceeding to C3+ until it passes. Old C6 is now a back-reference.
- Test that would have caught the bug: prefetchEnqueued > 0 is the assertion; it
  is exercised by the C2·V1 gate on the cluster (a from-cold SE run is required,
  which the macOS author machine cannot do).
- CI now repeats the full hello/SPP V1 path and requires both `pfIssued > 0` and
  `prefetchEnqueued > 0`, so source-side provenance regressions fail the build.
- Invariants held: no DRAM timing logic, no write-drain change, flags default off
  (the counter is unconditional but read-only accounting).

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

## FIX-2 (HIGH) — B2 demand-first was GLOBAL; changed to per-bank (PADC)

### Root cause (evidence in source)
B2 pre-pass, mem_ctrl.cc `demandFirst` block (pre-fix ~703-724): it built
`demandsOnly` = every queued demand read (all banks), ran FR-FCFS on it, and if
that returned any timing-ready demand it issued that demand. So **any ready
demand anywhere suppresses every prefetch** -- global demand-first. Cross-bank
FR-FCFS row-hit arbitration never runs while a ready demand exists, so a row-hit
prefetch to an idle bank can never be placed ahead of a row-miss demand in a
different bank.

### Two-bank thought experiment
Queue: demand D0 -> (rank0,bank0), prefetch P1 -> (rank0,bank1), P1 a row hit.
- D0 ready, row-miss; P1 ready, row-hit:
  - Global (pre-fix): `chooseNextFRFCFS(demandsOnly={D0})` returns D0 -> D0
    issues, P1 blocked. (B2 artificially strong: a row-miss demand beats a
    free row-hit prefetch on an independent bank.)
  - Per-bank (PADC): P1's bank (bank1) has no queued demand, so P1 is eligible;
    ordinary FR-FCFS over {D0, P1} is row-hit-first -> P1 (row hit) issues.
- D0 timing-blocked, P1 ready: BOTH policies issue P1 (pre-fix already fell
  through to full FR-FCFS when no demand was ready), so this case did not expose
  the difference -- the ready-demand case above is the discriminator.

### Decision (source wins; per plan recommendation)
Implement **per-bank demand-first** as B2 -- the PADC-faithful, reportable
baseline (Lee et al., "Prefetch-Aware DRAM Controllers": demand-vs-prefetch
priority is applied within a bank; across banks the normal row-hit FR-FCFS
arbiter decides). Replaced the global pre-pass rather than adding a
`demand_first_scope` flag (fewer moving parts; per-bank is the only baseline we
report). Work-conservation preserved: if nothing eligible is timing-ready, the
existing fall-through to full FR-FCFS still issues any ready command.

### Fix (applied)
- Pure eligibility predicate extracted to `mem/dprh_demand_first.hh`
  (`dprh::demandFirstEligible` / `demandFirstEligibility` + `bankKey`), so the
  semantics are unit-testable without a full MemCtrl.
- `demandFirst` block: build the set of banks holding >=1 queued demand, form
  the eligible sub-queue (all demands + prefetches to demand-free banks), run
  FR-FCFS on it; fall through to full FR-FCFS if nothing eligible is ready.
- Test `mem/dprh_demand_first.test.cc`: the two-bank scenario (bank0 demand +
  bank1 prefetch -> both eligible) and same-bank suppression (bank0 demand +
  bank0 prefetch -> prefetch ineligible) -- would have caught the global bug.
- Invariants held: no DRAM timing logic touched, no write-drain change,
  demand_first still default off (B1 unaffected).

## SPEC bring-up + MPKI profiling (Task 11) — AWAITING CLUSTER (data-dependent)
- SE-mode cross-compile: benchmarks/README.md (fill exclusion list on cluster).
- Profiler: scripts/mpki_profile.py (runs run_se.py --config B1 --prefetcher spp,
  computes LLC_MPKI = system.llc.demandMisses::total / (simInsts/1000)).
- Cluster: HANDOFF C14 (cross-compile) then C15 (profiling pass).
- FROZEN SUITES (fill from results/mpki_profile.csv on cluster; DO NOT invent):
    R2 main set (MPKI >= 1):        __________
    R3 no-harm controls (MPKI<0.5): __________
    R5 held-out (2-3, from R2, excluded from all tuning): [FIX-4: now emitted
      mechanically to results/HELD_OUT.md by mpki_profile.py -- copy from there,
      do NOT hand-pick.]
  These MUST be filled from measured MPKI on the cluster before Phase 1.

## FIX-4 (MEDIUM) — register held-out set mechanically at profiling time
### Root cause (evidence in source)
scripts/mpki_profile.py (pre-fix docstring lines 13-14 and final print lines
165-166) deferred R5 held-out selection to a manual step: "chosen by the user
from the R2 set and recorded in PHASE_LOG.md, not auto-selected here." So the
held-out set was (a) curated, not mechanical, and (b) unregistered at the one
natural point (C15 profiling) -- after which any tuning run could contaminate it,
with nothing to stop it. R5 (research_plan.md) requires 2-3 memory-intensive
traces excluded from ALL tuning; a manual, post-hoc list cannot enforce that.
### Fix (applied)
- mpki_profile.py: added a mechanical `select_held_out(r2_ranked)` -- from the R2
  set sorted by descending MPKI, take every 5th by rank (ranks 5,10,15), capped
  at 3; fallback = the single median-rank R2 member if R2 has < 5. The profiler
  now emits results/HELD_OUT.md (selected traces + the exact rule + provenance)
  as its final step, and its `--selftest` asserts the rule is deterministic and
  curation-free.
- scripts/dispatch_guard.py (new): reads HELD_OUT.md; `--check <bench>...` exits
  nonzero if any bench is held-out unless `--final-run` is passed; every
  `--final-run` invocation is appended to results/final_run.log (audit trail).
  `--selftest` asserts refuse-without-flag / allow-with-flag / allow-non-heldout.
- scripts/smoke_test.sh: routes its benches through dispatch_guard before running
  and accepts a leading `--final-run` to forward.
- Test/assertion that would have caught the gap: `dispatch_guard.py --selftest`
  and `mpki_profile.py --selftest` (both run on macOS/CI, no gem5).
- Dry-run demonstration (macOS, temp HELD_OUT.md with 605.mcf_s): held-out trace
  without --final-run => REFUSED (rc=3); with --final-run => ALLOWED (rc=0) and
  an audit line written to results/final_run.log; a non-held-out trace => ALLOWED
  (rc=0). Both selftests print OK. `python -m compileall scripts` clean.
- Invariants untouched (scripts only; no gem5, no timing, no flags).

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
    Source: HANDOFF C2·V1 -- system.mem_ctrl.prefetchEnqueued > 0 (FIX-3
    permanent counter). Corroborated by prefetchReadLatency::samples > 0.
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

---

## Phase 1 — Characterization

> **Ordering constraint:** All Phase-1 measured results and Gate G1 signing are
> gated behind **G0 PASSED** on the cluster. Do NOT interpret any authored stat
> as verified; do NOT sign G1 until G0 is signed and real-trace data exist.

### New and newly populated stats (Phase 1)

The six new/populated Phase-1 stats, and the Phase-0 stats they combine with:

| `stats.txt` name | Type | Phase | Notes |
|---|---|---|---|
| `system.mem_ctrl.demandReadsSeen` | Scalar | Phase 1 new | late-prefetch denominator |
| `system.mem_ctrl.latePrefetchDemands` | Scalar | Phase 1 new | demands arriving while a prefetch to their block is still queued |
| `system.mem_ctrl.agedDemandBlocked` | Scalar | Phase 1 populated | H_slot cycles where ≥1 demand aged ≥ A_guard (upper bound — see note) |
| `system.mem_ctrl.nonHslotReason::aged_demand` | Histogram bin | Phase 1 populated | alias of `agedDemandBlocked` in the non-H decomposition |
| `system.mem_ctrl.demandRowHits` | Scalar | Phase 1 populated | demand reads that hit an open DRAM row at issue time |
| `system.mem_ctrl.prefetchRowHits` | Scalar | Phase 1 populated | prefetch reads that hit an open DRAM row at issue time |

Phase-0 stats these combine with (already populated):

| `stats.txt` name | Role in derived metric |
|---|---|
| `system.mem_ctrl.schedCycles` | H_slot and decomposition denominator |
| `system.mem_ctrl.cyclesHslot` | true H_slot numerator |
| `system.mem_ctrl.cyclesReadyPrefetchNoDemand` | proxy / upper bound |
| `system.mem_ctrl.demandReadLatency::samples` | demand row-hit rate denominator |
| `system.mem_ctrl.prefetchReadLatency::samples` | prefetch row-hit rate denominator |

### Derived-metric formulas

```
H_slot fraction       = cyclesHslot / schedCycles
late-prefetch rate    = latePrefetchDemands / demandReadsSeen
demand row-hit rate   = demandRowHits / demandReadLatency::samples
prefetch row-hit rate = prefetchRowHits / prefetchReadLatency::samples
aged-demand frac      = agedDemandBlocked / schedCycles   (upper bound; see note)
```

### Measurement caveats (Phase 1-fix — READ BEFORE INTERPRETING H_slot)

Every H_slot number and the Gate G1 verdict depend on these:

1. **`schedCycles` = READ-scheduling DECISIONS, not clock ticks or write cycles**
   (gem5 `cd0624e`). `chooseNext` runs for the read queue (bus state READ) and,
   during write drain, for the write queue (bus state WRITE). The H_slot
   accounting is now gated to the READ bus state, so `schedCycles` counts only
   read-scheduling decisions — the correct H_slot denominator. It is a
   *per-decision* rate (one count per `nextReqEvent` reaching FR-FCFS read
   arbitration), NOT a fraction of wall-clock time. Before the fix, write-drain
   decisions inflated the denominator and the DEMAND_READY/NO_PREFETCH bins,
   understating H_slot in proportion to write traffic.

2. **H_slot is an ACCEPT-ALL upper bound, not an MSF-filtered measurement**
   (finding D). The Option-B filter's accuracy feedback (`noteUseful` /
   `noteEvicted`) is not wired, so `DprhFilter::accept()` accepts every prefetch
   (accuracy pinned at 100 ≥ `filter_accept_pct` 50). B1/B2 characterize H_slot
   over the full prefetcher stream — an **upper bound**; a real MSF-like filter
   would drop low-accuracy prefetches and generally lower H_slot. With the filter
   inert, **B1 and B2 differ only by `demand_first`** in Phase 1. Label every
   reported H_slot and the Gate G1 verdict "accept-all upper bound"; wiring the
   feedback (Task 13) is deferred to before final Phase 2 numbers (plan D2). See
   `docs/phase1-implementation-log.md` → "Finding D".

3. **Measure window integrity** (gem5 `d2e6076`): the `--measure` instruction
   window is real only because run_se.py now uses `scheduleInstStop`. Handoff step
   **P1b** (`check_inst_window.py`) guards it — run it after every gem5 rebuild
   before trusting any measured stat.

### Pre-registered R8 pressure metric

- **Metric:** `system.mem_ctrl.dram.busUtil` (DRAM channel utilization, %)
- **Threshold:** median B2 `busUtil` across the R2 memory-intensive suite.
  Registered here **before** looking at DPRH-relevant deltas (R8 requirement).

  pre-registered threshold: __________   (fill from cluster run of aggregate_hslot.py
                                          before examining any B2-vs-B1 delta)

  `aggregate_hslot.py` computes this median automatically and writes it to the
  CSV header; copy it here. A workload is `pressure_bin=high` iff its B2
  `busUtil` ≥ this threshold.

### A_guard used for Phase-1 aged-demand runs

- **Phase-1 value:** 64 cycles (passed as `--a-guard 64` in all P3/P4 handoff
  runs). This is a placeholder; the real Phase-3 sweep explores the full range.
- **Default `dprh_a_guard = 0`:** with A_guard = 0, every queued demand is
  trivially "aged" (age ≥ 0), so `agedDemandBlocked == cyclesHslot` — an upper
  bound. Phase-1 runs use 64 to obtain a non-trivial bin; treat all Phase-1
  aged-demand numbers as indicative, not authoritative.

---

### Gate G1 scaffold — AWAITING CLUSTER (DO NOT PRE-SIGN)

> G1 **must not** be signed until **G0 is PASSED** and real-trace data from the
> cluster P3 sweep exist. The three rows below are scaffolded per
> research_plan.md §5; fill `Measured` from `gate_g1.py` output; sign only after
> a human reviews cluster data and commits.

**Input:** median B2 `hslot_frac` across the R2 memory-intensive set (all R2
workloads NOT in the R3 no-harm controls), as reported by
`scripts/gate_g1.py results/phase1_hslot.csv --controls-file <R3 controls>`.

**Regime thresholds (research_plan.md §5):**

| Regime | Condition | Phase-2 direction |
|---|---|---|
| PIVOT | median B2 H_slot < 2% | Pure characterization (Outcome O3); do NOT build MVP-0 as a performance mechanism |
| PROCEED — timeliness framing | 2% ≤ median B2 H_slot < 8% | Proceed to Phase 2; frame gains as timeliness / bandwidth-efficiency first, IPC second |
| PROCEED — performance framing | median B2 H_slot ≥ 8% | Proceed to Phase 2 with performance framing |

**Gate G1 measurements (fill from cluster):**

- G1 — median B2 H_slot on the R2 memory-intensive set
    Source: `python3 scripts/gate_g1.py results/phase1_hslot.csv --controls-file <R3 file>`
    Measured: __________    Verdict: AWAITING CLUSTER

- G1 (per-workload) — H_slot by workload and pressure bin
    Source: `results/phase1_hslot.csv` (written by `aggregate_hslot.py`)
    Measured: see CSV    Verdict: AWAITING CLUSTER

- G1 (late-prefetch) — median late-prefetch rate on R2 memory-intensive set
    Source: `late_prefetch_rate` column in `results/phase1_hslot.csv`
    Measured: __________    Verdict: AWAITING CLUSTER

### Sign-off (only when G0 PASSED and all cluster measurements exist)
- If median < 2%: set "G1: PIVOT (<date>)" with the median, per-workload table,
  the regime justification, and the cluster evidence; commit
  "dprh(phase1): Gate G1 evaluated — PIVOT".
- If 2% ≤ median < 8%: set "G1: PROCEED — timeliness framing (<date>)" similarly.
- If ≥ 8%: set "G1: PROCEED — performance framing (<date>)" similarly.
- DO NOT start Phase 2 until G1 is signed. If G0 has not passed, this scaffold
  has no standing data and nothing here may be interpreted as a verdict.
