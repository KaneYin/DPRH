# DPRH Phase 1 — Implementation Log

Running log of Phase 1 implementation against `plan/phase1_implementation_plan.md`.
Authoring machine: macOS. Build/measure: Linux cluster (not accessible here).
Steps tagged `[cluster]` are authored but not executed; see
`results/CLUSTER_HANDOFF.md` (P-series) for the ordered command list.

Phase 1 = characterization instrumentation: completing the H_slot decomposition
with the aged-demand bin (AGED_DEMAND), adding the late-prefetch rate counter,
populating the demand/prefetch row-hit split, writing a synthetic attribution
surface (3×3 R6 factorial), an aggregation pipeline, and a Gate G1 scaffold.
All instrumentation is read-only accounting; no `DRAMInterface`/`mem_interface`
timing code, write-drain policy, or `dprh_common.FROZEN` value is touched.
Authoring and unit tests were done on macOS and exercise on CI; all measured runs
(real-trace sweep, factorial cluster runs, Gate G1 verdict) are `[cluster]`
handoff items gated behind **G0 PASSED**.

## Git layout
- gem5 tree edits (`gem5/src/**`, `gem5/configs/dprh/**`) are committed **inside
  the nested gem5 git repo** (branch `stable`; diffable vs stock base).
- Repo-root tooling (`scripts/`, `results/`, `docs/`, `plan/`) is committed in
  the **outer** repo on branch `v0`.
- All Phase 1 commits use `dprh(phase1): <summary>`.

## Hard invariants held
- `src/mem/dram_interface.cc`, `DRAMInterface.py`, `mem_interface.cc`: **untouched**.
- Write-drain policy / thresholds: **untouched**.
- DPRH gate `dprhChooseNext`: Phase 0 **no-op** (`return queue.end()`) — unchanged.
- All new Phase 1 stats are **read-only accounting** (no parallel timing model;
  heeds the research_plan.md §3 warning).
- No `dprh_common.FROZEN` value changed. (`dprh_a_guard` is a non-frozen
  Phase-3 sweep knob, not a frozen system parameter.)

## Task status

| Task | Status | Notes |
| --- | --- | --- |
| 1 Late-prefetch predicate + unit test | COMPLETE (author) | gem5 `ca8e2f3af5`, `a2638a86a5`; CI compiles; cluster runs |
| 2 Populate late-prefetch rate in MemCtrl | COMPLETE (author) | gem5 `e488a1636e`, `7e8ca477dc`; CI compiles; cluster verifies |
| 3 Populate demand/prefetch row-hit split | COMPLETE (author) | gem5 `6170f1e570`; CI compiles; cluster verifies |
| 4 Aged-demand predicate + unit test | COMPLETE (author) | gem5 `990780c2a9`; CI compiles; cluster runs |
| 5 Populate aged-demand bin in H_slot pass | COMPLETE (author) | gem5 `eff596f5ac`; CI compiles; cluster verifies non-zero |
| 6 Thread --a-guard knob + factorial scripts | COMPLETE (author) | gem5 `0e4aeb0e19`; outer `b8103cb`; selftest passes macOS; cluster runs factorial |
| 7 Real-trace per-workload aggregation | COMPLETE (author) | outer `cceb5f9`; selftest passes macOS; cluster data awaited |
| 8 Gate G1 evaluation scaffold | COMPLETE (author) | outer `4283882`, `cbbb89e`; selftest passes macOS; verdict AWAITING CLUSTER |
| 9 Bookkeeping (this log, PHASE_LOG, CLUSTER_HANDOFF) | COMPLETE (author) | outer repo; this commit |

## Key interfaces added

### Pure predicate headers (gem5 tree)
- `dprh::blocksMatch(a, b, burstSize)` — burst-aligned block equality
  (`gem5/src/mem/dprh_late.hh`)
- `dprh::QueuedRead{addr, isPrefetch}` — lightweight read-queue snapshot entry
  (`gem5/src/mem/dprh_late.hh`)
- `dprh::demandHasQueuedPrefetch(queued, demandAddr, burstSize)` — true iff an
  accepted prefetch to the incoming demand's block is still queued
  (`gem5/src/mem/dprh_late.hh`); unit-tested in `dprh_late.test.cc`
- `dprh::hslotAgedBlocked(hslot, anyAgedDemand)` — true iff a true H_slot cycle
  also has an aged (≥ A_guard) queued demand; added to
  `gem5/src/mem/dprh_hslot.hh`; unit-tested in `dprh_hslot.test.cc` (new case)

### MemCtrl additions (gem5 tree)
- `MemCtrl::hasAgedDemand(queue, mem_intr)` — returns true if any queued demand
  has aged ≥ `dprhAGuard` cycles (read-only; `gem5/src/mem/mem_ctrl.{hh,cc}`)
- New stats (Scalars): `demandReadsSeen`, `latePrefetchDemands`
  (`gem5/src/mem/mem_ctrl.{hh,cc}`)
- Populated stats: `agedDemandBlocked`, `nonHslotReason::aged_demand` (in the
  H_slot pass), `demandRowHits`, `prefetchRowHits` (at the read-issue site)

### Config harnesses (gem5 tree)
- `make_mem_ctrl(config, a_guard=None)` — optional `a_guard` forwarded to
  `ctrl.dprh_a_guard` (`gem5/configs/dprh/dprh_common.py`)
- `--a-guard` CLI argument added to `gem5/configs/dprh/run_se.py` and
  `gem5/configs/dprh/run_trafficgen.py`

### Analysis scripts (repo root)
- `scripts/hslot_factorial.sh` — drive the synthetic 3×3 R6 factorial (B1 & B2)
  into `results/runs/factorial/`; usage: `hslot_factorial.sh <gem5.opt> [A_GUARD]`
- `scripts/analyze_hslot_surface.py` — read factorial cells, compute
  `H_slot = cyclesHslot/schedCycles`, write `results/phase1_surface.csv`, assert
  A1/A2 attribution monotonicity; `--selftest` passes on macOS
- `scripts/aggregate_hslot.py` — per-workload Phase-1 table across the R2 suite;
  R8 bandwidth-pressure binning by B2 `dram.busUtil`; `--selftest` passes on macOS
- `scripts/gate_g1.py` — apply the §5 pivot thresholds to B2 real-trace H_slot;
  emits verdict (never auto-signs); `--selftest` passes on macOS; tolerates
  non-numeric `hslot_frac` cells with a stderr warning (skips, does not crash)

## Plan-vs-reality adaptations

1. **Late-prefetch scan is in place, not via the vector wrapper.** The plan's
   Task-2 call site built a `std::vector<QueuedRead>` copy of the whole read
   queue per demand enqueue; code review flagged the per-enqueue heap allocation
   on the simulator hot path. Final code (gem5 commit `7e8ca477dc`) scans
   `readQueue` in place with early-exit using the unit-tested `dprh::blocksMatch`,
   mirroring how the sibling `dprh_demand_first` uses its per-item predicate in
   production while the vector wrapper stays test-only.
   `demandHasQueuedPrefetch` (the plan's spec over `std::vector<QueuedRead>`)
   remains the sole unit-tested definition of "late prefetch" — it is not
   replaced, only not used on the hot path.

2. **`blocksMatch` power-of-2 precondition** is documented with a `@pre`
   annotation (gem5 commit `a2638a86a5`) and the Task-2 call site relies on it
   (DDR4 `bytesPerBurst()` is always a power of two, guaranteeing the mask
   idiom's correctness). The per-call assertion that would have guarded the hot
   path was not committed; the precondition is instead enforced at the architectural
   level and noted in the header.

3. **Aged-demand bin is an UPPER BOUND.** `MemCtrl::hasAgedDemand` counts any
   demand aged ≥ A_guard regardless of *why* it is not command-ready this cycle,
   so `agedDemandBlocked` over-counts vs a strict "aged AND blocked" interpretation.
   This is acceptable under the read-only accounting constraint and is consistent
   with the plan's stated intent (the bin sizes the Phase-3 guard sweep, not a
   precise suppression count). With the default `dprh_a_guard = 0` every queued
   demand is "aged" (age ≥ 0 is trivially true), so `agedDemandBlocked ==
   cyclesHslot` at default settings. Phase-1 aged-demand runs therefore pass a
   non-zero `--a-guard` (handoff uses 64 cycles); the real sweep is Phase 3.

4. **`_safe_div` numerator guard in `aggregate_hslot.py`.** The plan's
   `aggregate_hslot.py` `_safe_div` only guarded the denominator (crashes when an
   optional stat key is absent from a run, e.g. a B1 run producing no
   `cyclesReadyPrefetchNoDemand`). Final code guards both operands:
   `(num / den) if (num is not None and den and den > 0) else None`, realizing the
   plan's stated "missing stats yield None, never crash" intent. Behaviour is
   identical for valid numeric inputs.

5. **`gate_g1.py` tolerates non-numeric `hslot_frac` cells** (outer commit
   `cbbb89e`). The initial implementation (commit `4283882`) would raise
   `ValueError` on a cell containing the string "None" or empty string — possible
   when `aggregate_hslot.py` writes a row with a missing denominator. Final code
   skips those cells with a stderr warning rather than crashing, so the evaluator
   degrades gracefully on partial data.
