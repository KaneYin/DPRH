# Option B (MSF-like filter) — Phase 0 limitations (documented, intentional)

Phase 0 ships **Option B**, a stand-in for the MSF/PAM perceptron (Option A,
research_plan.md D2). Option B exposes the *same interface* Option A will use — a
single `accept()`/drop decision per prefetch at read-queue enqueue, plus
`noteUseful()`/`noteEvicted()` feedback hooks — so Option A can replace it later
without touching DPRH.

## What Phase 0 Option B does
- `DprhFilter::accept()` decides accept/drop on a running prefetcher-accuracy
  estimate (MSF feature #7 — the strongest single memory-side signal at the MC),
  recomputed every `filter_epoch` decisions, thresholded at `filter_accept_pct`.
- Hooked in `MemCtrl::addToReadQueue` (mem_ctrl.cc, top of function): only
  `pkt->req->isPrefetch()` packets are candidates; on drop we call
  `accessAndRespond(pkt, frontendLatency, mem_intr)` (the exact "respond without
  a timed DRAM access" path gem5 already uses for write-queue-serviced reads),
  which turns the packet into a response and frees the upstream (L2) MSHR.
- Constructed only when `enable_filter` is True (B1/B2/DPRH). An unflagged build
  allocates no filter and is byte-identical to stock gem5.

## Demand-upgrade invariant (preserved)
A prefetch whose address later matches a true demand is never treated as
filtered: demands are never candidates for dropping, and an in-flight prefetch
matched by a later demand is handled by gem5's existing `isInWriteQueue` /
read-coalescing paths in `recvTimingReq`, not by the filter.

## Known Phase 0 limitation: feedback wiring is STUBBED
`noteUseful()` / `noteEvicted()` exist and are unit-tested, but are **not yet
wired** to the LLC used/evicted signals in Phase 0. Consequences:
- `cachedAccuracy` stays at its optimistic warm-up value (100), so with the
  default `filter_accept_pct=50` the filter **accepts** every prefetch and
  `filterDroppedPrefetches` stays 0. This is the intended B1/B2 behavior for
  Phase 1 characterization (the filter accepts the stream; drop behavior is not
  exercised in Phase 1).
- This is acceptable because Phase 1 instruments B1/B2 (filter accepts) and does
  **not** depend on drop behavior. Option B drop behavior / Option A perceptron
  is a Phase 1/2 concern.

## Exact hook points for later feedback wiring (Task 13 / Phase 2)
- `DprhFilter::noteUseful()` — call on an LLC prefetch-hit (line prefetched then
  demanded). Anchor: cache prefetch-hit notification in
  `src/mem/cache/base.cc` (prefetch stats update on access to a prefetched blk).
- `DprhFilter::noteEvicted()` — call when a prefetched, never-used line is
  evicted from the LLC. Anchor: cache eviction path in `src/mem/cache/base.cc`
  (`BaseCache::evictBlock` / invalidate), gated on the block's "prefetched but
  unused" bit.
- The MC does not directly see LLC eviction/use today; wiring requires either a
  probe/notify from the LLC prefetcher stats or a small callback registered by
  the MC. Deferred to Task 13 (only if G0 requires it) / Phase 2.

## Interface stability (why Option A drops in cleanly)
`DprhFilter` depends on nothing MSF-internal. Option A replaces the body of
`accept()` (and the training in `noteUseful`/`noteEvicted`) with the perceptron;
the MC call site in `addToReadQueue` and the demand-upgrade invariant are
unchanged.
