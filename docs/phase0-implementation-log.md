# DPRH Phase 0 — Implementation Log

Running log of Phase 0 implementation against `plan/phase0_implementation_plan.md`.
Authoring machine: macOS. Build/measure: Linux cluster (not accessible here).
Steps tagged `[cluster]` are authored but not executed; see
`results/CLUSTER_HANDOFF.md` for the ordered command list.

Frozen gem5 base: `51edbbb9cfd37e92e9901aea2caa4a8f20eda005` (tag v25.1.0.1).
Decisions locked to plan defaults: **D-A0 = SPP primary**, **D-A0b = DDR4_2400_16x4**.

## Git layout
- gem5 tree edits (`gem5/src/**`, `gem5/configs/dprh/**`) are committed **inside
  the nested gem5 git repo** (remote `upstream` = gem5/gem5, diffable vs stock).
- Repo-root tooling (`scripts/`, `tests/`, `results/`, `benchmarks/README.md`,
  `plan/refs/`, `.gitignore`) is committed in the **outer** repo on branch `v0`.
- All commits use `dprh(phase0): <summary>`.

## Hard invariants held
- `src/mem/dram_interface.cc`, `DRAMInterface.py`, `mem_interface.cc`: **untouched**.
- Write-drain policy / thresholds: **untouched**.
- New gating flags `enable_dprh`/`demand_first`/`enable_filter`: default **False**.
- DPRH gate `dprhChooseNext`: Phase 0 **no-op** (`return queue.end()`).

## Task status
| Task | Status | Notes |
| --- | --- | --- |
| 1 Version control, A0, PHASE_LOG | COMPLETE | base commit frozen, upstream remote added |
| 2 Build gem5.opt | AUTHORED / cluster | build wrapper written; build = HANDOFF C1/C2 |
| 3 Frozen sys def + B0 runner | COMPLETE (author) | run = C3 |
| 4 B1 SPP prefetches | AUTHORED / cluster | wiring in run_se.py; run = C4/C5 |
| 5 V1 prefetch-flag | AUTHORED / cluster | doc + static analysis; run = C6 |
| 6 MemCtrl params + gate seam | COMPLETE (author) | default-off by construction; diff = C7 |
| 7 Option B filter | COMPLETE (author) | filter+test+hook; unit test/run = C8/C9 |
| 8 B2 demand-first | COMPLETE (author) | frfcfs pre-pass; B1!=B2 run = C10 |
| 9 Stats framework | COMPLETE (author) | H_slot inputs + latency histos; presence = C11 |
| 10 Synthetic streams + tag patch | COMPLETE (author) | R6 tag + calibration; sweep = C12/C13 |
| 11 SPEC bring-up + MPKI | AUTHORED / cluster | cross-compile+profile = C14/C15 (data-dependent) |
| 12 3-trace smoke | AUTHORED / cluster | driver written; run = C16 (data-dependent) |
| 13 Filter feedback wiring | CONDITIONAL SKIP | fires only if 9/12 not green on cluster |
| 14 Gate G0 | SCAFFOLDED / cluster | four conditions AWAITING CLUSTER; no verdict claimed |

## Plan-vs-reality adaptations (grounded in real gem5 source)
1. **`prefetch_on_access`** is a `BasePrefetcher` param (Prefetcher.py:80), not a
   Cache param as the plan phrased it. Set on the prefetcher object in
   `dprh_common.make_prefetcher`. (Task 3)
2. **Filter unit test path**: placed at `gem5/src/mem/dprh_filter.test.cc`
   (co-located with the SConscript that registers it) instead of the plan's
   `tests/dprh/filter.test.cc`, per gem5 GTest convention and the plan's own
   Step-4 registration string. (Task 7)
3. **Row-hit split stats** (`demandRowHits`/`prefetchRowHits`) are declared but
   populated in Phase 1: computing them at the MC would require reading DRAM
   bank open-row state, and `DRAMInterface` is a no-touch invariant. The
   load-bearing, grepped stats (schedCycles/cyclesNoLegalDemand/cyclesHslot/
   demand+prefetch latency histos/nonHslotReason) are populated in Phase 0. (Task 9)
4. **`createDram` prefetch tag** passed **positionally** (15th arg) from
   `run_trafficgen.py`: `PyBindMethod("createDram")` binds the raw C++ pointer
   and does not surface C++ default args to Python. Stock in-tree callers use the
   C++ default and are unaffected. (Task 10)
5. **V1**: static analysis of `Cache::createMissPacket` (cache.cc:553) shows the
   outbound miss packet reuses the same `req`, so the PREFETCH flag is *expected*
   to survive to the MC without a forwarding patch — but this is left for the
   cluster to confirm empirically; no patch committed. (Task 5)

## Key interfaces added
- `DprhFilter` (`gem5/src/mem/dprh_filter.hh`): `accept()`, `noteUseful()`,
  `noteEvicted()`, `accuracyPct()`. Option-A-replaceable behind the same call.
- `MemCtrl::dprhChooseNext(queue, extra_col_delay, mem_intr)`: Phase 0 no-op gate.
- `MemCtrl::hasLegalDemand(queue, mem_intr)`: H_slot predicate helper (unit-testable).
- MemCtrl params: `enable_dprh`, `demand_first`, `enable_filter`, `dprh_a_guard`,
  `dprh_kp`, `filter_accept_pct`, `filter_epoch` (all default-off/stock).
- `DramGen` param `tag_prefetch` (default false) + `createDram` trailing arg.
- Config scripts: `run_se.py` (B0/B1/B2/DPRH SE runner), `run_trafficgen.py`
  (R6 synthetic harness), `dprh_common.py` (frozen system).
