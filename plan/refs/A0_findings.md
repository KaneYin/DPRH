# A0 — RESOLVED (from *Le — Generation Memory Systems*, Chapter 5, pp. 91–140)

A0 in the research plan ("confirm MSF's benchmark suite/version and baseline
prefetcher") is answered from *Le — Generation Memory Systems*, Chapter 5
(`./Le - Generation Memory Systems copy.pdf`, pp. 91–140). These findings are
frozen into the Phase 0 plan and reproduced here verbatim as the auditable A0
artifact the thesis references.

| A0 question | Finding (Chapter 5) | Decision for DPRH |
| --- | --- | --- |
| Benchmark suite/version | **SPEC CPU2006 + CPU2017 both**, categorized by LLC MPKI; 55 multi-app workload *sets* (37 HIGH, 18 MIX). SimPoint3.0 regions, 1B-instruction detailed runs. (pp. 94, 114–115, Tables XII–XIII) | Use single-benchmark components drawn from **both 2006 and 2017** (DPRH main results are single-core, §2 of research plan). R2 suite is still selected by DPRH's own criterion (per-benchmark LLC MPKI ≥ 1), not MSF's set-level > 10. |
| Baseline prefetcher | **Signature-Path Prefetcher (SPP)** is the representative prefetcher (Fig. 22, p. 92); MSF claims generality across prefetchers but reports SPP as the featured baseline. (pp. 72, 75) | **DECISION D-A0 (flagged for user confirmation):** set **primary prefetcher = SPP** (`SignaturePathPrefetcher`), **sensitivity prefetcher = Stride**. This *inverts* research_plan.md §3.2, which listed Stride primary / SPP sensitivity. Rationale: R1 comparability to the MSF foundation. gem5 ships `signature_path` and `signature_path_v2`. |
| Core / topology | **8 OoO cores, 4.0 GHz, 8-issue**; L1 32 KB 8-way/16 MSHR; L2 256 KB **4-way**/48 MSHR; L3 **16 MB** 8-way/64 MSHR; **DDR4-3200** 22-22-22; MC FR-FCFS, R/W queue **64/64**, 20 ns overhead. (Table XI, p. 92) | DPRH main results are **single-core** by design (research plan §2). Keep research_plan.md §3.2 single-core cache sizing (LLC 2 MB/16-way). **Log every divergence** from MSF in `PHASE_LOG.md`: L2 associativity (8-way vs MSF 4-way), LLC size (2 MB vs 16 MB shared), DRAM speed (see D-A0b). |
| MC / scheduler | FR-FCFS, 64/64 queues, matches research plan. | No change. `read_buffer_size = 64`. |
| Filter (MSF/PAM) design | PAM at MC over the read stream; perceptron with **8 selected features** (Table VII, p. 86), **activation threshold = 16**; **Positive table 1024 / Negative table 512** entries, 4-way, LRU, 5-bit tag; trains on LLC used/evicted feedback; **prefetch→demand upgrade on address match** (never drops a line with a true demand); dropped prefetch signals its MSHR can free. (pp. 78–90) | This is the **Option A** target. Phase 0 ships **Option B** (stand-in), which must expose the *same interface* — a single `accepted` bit per prefetch decided at read-queue enqueue, with the demand-upgrade invariant preserved — so Option A can replace it later without touching DPRH. |

## DECISION D-A0b (flagged)

research_plan.md §3.2 froze `DDR4_2400_16x4`; MSF used DDR4-3200. This plan keeps
`DDR4_2400_16x4` as the frozen default (well-validated gem5 stock config) and
records the divergence. If the user prefers tighter MSF comparability, switch to
a gem5 3200 config in Task 3 *before* the G0 smoke test (changing it later
invalidates all numbers).

**Confirm D-A0 and D-A0b before Task 3.**

## Confirmation status (Phase 0 execution)

- **D-A0 = SPP primary prefetcher** — CONFIRMED (locked to plan default).
- **D-A0b = DDR4_2400_16x4** — CONFIRMED (locked to plan default).
