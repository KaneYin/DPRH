# V1 — verify `Request::PREFETCH` reaches `MemCtrl`

## Purpose

DPRH classifies requests at `MemCtrl` with `pkt->req->isPrefetch()`. If a
hardware prefetch loses that provenance, the Option B filter, demand-first
baseline, late-prefetch accounting, and H_slot statistics all operate on the
wrong demand/prefetch partition. C2·V1 is therefore a hard gate before C3+.

The permanent runtime signal is
`system.mem_ctrl.prefetchEnqueued`. A valid V1 run must satisfy both:

- `system.cpu.l2cache.prefetcher.pfIssued > 0`
- `system.mem_ctrl.prefetchEnqueued > 0`

## Root cause confirmed by the first cluster run

Cluster commit `1aa651d01a` produced `pfIssued = 499` but
`prefetchEnqueued = 0`.

The queued hardware-prefetch path created a `Request` with flags `0` and used
`MemCmd::HardPFReq` only on the initial packet. On a cache miss,
`Cache::createMissPacket` changed the command to an ordinary read while reusing
the untagged `RequestPtr`. Consequently no persistent provenance remained when
the packet reached `MemCtrl`.

The older analysis noticed that `createMissPacket` reused the request, but
incorrectly assumed the request had already been marked. TrafficGen did not
expose this source-side bug because its `--pf-tag` path explicitly constructs
the request with `Request::PREFETCH`.

## Repair

`QueuedPrefetcher.mark_request_as_prefetch` is an explicit, default-false
parameter. When enabled, `Queued::DeferredPacket::createPkt` sets
`Request::PREFETCH` on the generated hardware-prefetch request. DPRH enables
the parameter only for its SPP and Stride profiles, preserving stock behavior
for all other queued-prefetcher configurations.

The flag then follows the existing cache path without a forwarding patch:
`Cache::createMissPacket` reuses the marked `RequestPtr` when it replaces
`HardPFReq` with the downstream read command.

## Cluster verification

Build the modified C++ binary, then run the C2·V1 command in
`results/CLUSTER_HANDOFF.md`. Do not reuse the binary compiled for
`1aa651d01a`; this repair changes `queued.cc` and generated SimObject params.

Verdict:

- `pfIssued > 0` and `prefetchEnqueued > 0`: PASS; record G0.c and continue.
- `pfIssued == 0`: FAIL; the workload did not exercise the L2 prefetcher.
- `pfIssued > 0` and `prefetchEnqueued == 0`: FAIL; stop before C3.

## Result log

- 2026-08-09, gem5 `1aa651d01a`: `pfIssued = 499`,
  `prefetchEnqueued = 0` — **FAIL**, source request was unmarked.
- Post-repair cluster commit: __________
- `pfIssued`: __________
- `prefetchEnqueued`: __________
- V1 verdict: __________
