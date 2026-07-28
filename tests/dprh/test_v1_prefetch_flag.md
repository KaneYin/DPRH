# V1 — verify `Request::PREFETCH` survives to `MemCtrl`

**Purpose.** DPRH and the Option B filter are dead code if `pkt->req->isPrefetch()`
is false at the memory controller. V1 is the single most load-bearing Phase 0
check: it confirms the PREFETCH flag set by the L2 prefetcher reaches
`MemCtrl::addToReadQueue`.

This is a **[cluster]** verification (requires building gem5.opt and running). The
temporary probe below is authored here so the cluster operator can re-apply it,
build, run, observe, then remove it. It is **not** committed into the C++ source
(plan Task 5 Step 5 removes the probe before commit; the permanent replacement is
`prefetchReadLatency::samples > 0` from Tasks 9/10).

---

## Code-path analysis (authored on macOS; confirms V1 is *expected* to PASS)

Grounded in real gem5 v25.1.0.1 source:

- `Request::isPrefetch()` is a property of the `Request` object
  (`src/mem/request.hh:1029`), not of the `Packet`.
- On an L2 miss, the outbound memory packet is built in
  `Cache::createMissPacket` (`src/mem/cache/cache.cc:493`) via
  `new Packet(cpu_pkt->req, cmd, blkSize)` (`cache.cc:553`) — it **reuses the
  same `req` (RequestPtr)**. Because the PREFETCH flag lives on `req`, it is
  carried unchanged to the next level and to the MC.

Conclusion: the flag is expected to survive to `MemCtrl` **without** a forwarding
patch. This must still be confirmed empirically on the cluster (do not assume).
If the count is zero, apply the Step-3 patch below.

---

## Step 1 — TEMP probe (re-apply on cluster, then remove)

In `gem5/src/mem/mem_ctrl.cc`, in `MemCtrl::addToReadQueue` (anchor cc:189),
immediately after the `MemPacket* mem_pkt = mem_intr->decodePacket(...)`
construction (cc:257–259), add:

```cpp
    // TEMP V1 probe (remove after V1 confirmed): count prefetch-flagged reads.
    if (pkt->req->isPrefetch()) {
        DPRINTF(MemCtrl, "V1: prefetch-flagged read at MC addr %#x\n",
                pkt->getAddr());
        ++stats.v1PrefetchReadsSeen;   // temp Stats::Scalar (see below)
    }
```

Add a temporary scalar to the `CtrlStats` struct in `gem5/src/mem/mem_ctrl.hh`
(near `servicedByWrQ`):

```cpp
        statistics::Scalar v1PrefetchReadsSeen;
```

and register it in `CtrlStats::CtrlStats` (`mem_ctrl.cc` ctor, near the
`ADD_STAT(servicedByWrQ, ...)` block at cc:1196):

```cpp
    ADD_STAT(v1PrefetchReadsSeen, statistics::units::Count::get(),
             "TEMP V1: prefetch-flagged reads observed at the MC"),
```

## Step 2 — Build and run B1/SPP with the MemCtrl debug flag (cluster)

```bash
./scripts/build_gem5.sh
./gem5/build/X86/gem5.opt --debug-flags=MemCtrl --outdir=m5out/v1 \
  gem5/configs/dprh/run_se.py --config B1 --prefetcher spp \
  --cmd gem5/tests/test-progs/hello/bin/x86/linux/hello \
  --ff-offset 0 --warmup 0 --measure 5000000 2>&1 | grep -c "V1: prefetch-flagged"
grep v1PrefetchReadsSeen m5out/v1/stats.txt
```

**Expected (PASS):** both counts > 0 — the PREFETCH flag reaches the MC.

## Step 3 — If count == 0, patch flag forwarding

The flag is being dropped crossing the LLC/membus. Investigate in order:
1. Confirm SPP/BasePrefetcher sets `Request::PREFETCH` on generated packets:
   `git grep -n "PREFETCH" gem5/src/mem/cache/prefetch/`.
2. Confirm the LLC forwards the flag on miss-generated requests to memory:
   `git grep -n "createMissPacket\|MemCmd::.*Prefetch\|allocateMissBuffer" gem5/src/mem/cache/`.
   (Per the analysis above, `createMissPacket` already reuses `req`, so this is
   the primary suspect only if some intermediate path rebuilds the request.)
Patch the specific site that recreates the outbound packet so it copies the
PREFETCH flag onto the forwarded `req`. Record the file/line of the fix here.

## Step 4 — Remove the TEMP probe (keep any forwarding fix)

Delete the probe and the `v1PrefetchReadsSeen` scalar. The permanent V1 evidence
is `prefetchReadLatency::samples > 0` (Task 9 histogram; Task 10 synthetic run).

---

## Result log (fill on cluster)

- grep -c "V1: prefetch-flagged" output: __________
- v1PrefetchReadsSeen value: __________
- Forwarding patch applied? (file:line or "none needed"): __________
- V1 verdict (PASS/FAIL): __________
