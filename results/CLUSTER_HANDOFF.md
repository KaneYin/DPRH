# DPRH Phase 0 — Cluster Handoff

This file lists, **in execution order**, every `[cluster]` command that must be
run on the Linux cluster (where `gem5.opt` is compiled and all measured runs
happen). The macOS authoring machine cannot run any of these. For each command:
the exact invocation, the working directory, and the expected result.

> Author machine: macOS (no scons / gem5.opt / SPEC / traffic-gen access).
> All commands below are run from the repo root on the cluster after `git pull`
> of both the outer repo (branch `v0`) **and** the inner `gem5/` repo (which
> carries the DPRH C++/config commits on top of frozen base
> `51edbbb9cfd37e92e9901aea2caa4a8f20eda005`, tag v25.1.0.1).

Note on git layout: DPRH edits under `gem5/` are committed **inside the nested
gem5 git repo** (remote `upstream` = gem5/gem5, diffable against stock). Repo-root
tooling (`scripts/`, `tests/`, `results/`, `.gitignore`) is committed in the
**outer** repo on branch `v0`. Push/pull both.

---

## C0 — Getting the code onto the cluster (do this first)

**Current reality (2026-07-28):** there is **no writable git remote** for either repo.
- Outer repo `/Users/kane/DPRH` (branch `v0`, 13 commits): **no remotes**.
- Nested `gem5/` (branch `stable`, 6 DPRH commits on base `51edbbb9cf`): only remote
  is the read-only public `github.com/gem5/gem5.git` — you cannot push to it.

So a plain `git pull` on the cluster will NOT work yet. Pick one transfer path:

### Option A (recommended, no server needed) — `git bundle` both repos
On the **Mac**, from `/Users/kane/DPRH`:
```bash
git bundle create /tmp/dprh-outer.bundle --all           # outer repo (branch v0)
git -C gem5 bundle create /tmp/dprh-gem5.bundle --all     # nested gem5 (branch stable)
scp /tmp/dprh-outer.bundle /tmp/dprh-gem5.bundle <cluster>:~/
```
On the **cluster** (first time):
```bash
git clone -b v0 ~/dprh-outer.bundle DPRH
cd DPRH
rm -rf gem5                                               # remove empty placeholder if any
git clone -b stable ~/dprh-gem5.bundle gem5
```
On later syncs, re-bundle on the Mac and on the cluster run:
```bash
cd ~/DPRH        && git pull ~/dprh-outer.bundle v0
cd ~/DPRH/gem5   && git pull ~/dprh-gem5.bundle stable
```

### Option B (simplest, ignores git) — rsync the whole tree
Everything is committed, so a working-tree copy carries all code. From the **Mac**:
```bash
rsync -av --exclude 'gem5/build/' --exclude 'm5out/' \
      /Users/kane/DPRH/ <cluster>:~/DPRH/
```
Re-run the same rsync after each edit session. (No git history on the cluster, but
the build/run commands below only need the working tree.)

### Option C (proper fix, later) — create writable remotes
Create a private repo you own for the outer project and, separately, push the gem5
DPRH branch to your own gem5 fork; then `git clone` + `git remote` on the cluster.
This is the "decide later" item from Phase 0 planning (submodule vs two-repo).

**Verify after transfer** (cluster): `git -C ~/DPRH/gem5 log --oneline -1` shows
`dprh(phase0): synthetic streams ...`, and `ls ~/DPRH/gem5/src/mem/dprh_filter.hh` exists.

---

## Order of execution

### C1 — Task 2 Step 2: Build gem5.opt
```bash
./scripts/build_gem5.sh 2>&1 | tee results/build.log
```
Expected: ends with `... build/X86/gem5.opt` linked; `gem5/build/X86/gem5.opt` exists.
After success: fill `results/PHASE_LOG.md` "built on <cluster hostname>" field.

### C2 — Task 2 Step 3: Smoke-run stock gem5
```bash
./gem5/build/X86/gem5.opt gem5/configs/deprecated/example/se.py \
  --cmd=gem5/tests/test-progs/hello/bin/x86/linux/hello 2>&1 | tail -5
```
Expected: prints `Hello world!` and `Exiting @ tick ...`.
After C1+C2: commit PHASE_LOG update (`dprh(phase0): cluster build wrapper; record gem5.opt build`).

### C2·V1 — V1 GATE (FIRST DATA-PRODUCING RUN — must pass before C3+)
**HARD GATE. Do NOT run C3 or anything after it until this passes.** The PREFETCH
flag must survive from the L2 prefetcher down to the MemCtrl; if it does not, the
Option B filter and *every* prefetch-classified stat (H_slot included) are dead
code and C3–C17 produce plausible-looking garbage. Uses the permanent
`prefetchEnqueued` counter (FIX-3) — no temp probe or extra rebuild needed.
```bash
./gem5/build/X86/gem5.opt --outdir=m5out/v1 gem5/configs/dprh/run_se.py \
  --config B1 --prefetcher spp \
  --cmd gem5/tests/test-progs/hello/bin/x86/linux/hello \
  --ff-offset 0 --warmup 0 --measure 5000000 2>&1 | tail -3
grep -E "l2cache.*(pfIssued|num_hwpf|prefetch.*issued)" m5out/v1/stats.txt | head
grep system.mem_ctrl.prefetchEnqueued m5out/v1/stats.txt
```
Verdict (self-contained — this run also exercises prefetch issue, folding in C5):
- **PASS**: `prefetchEnqueued > 0` → the flag reaches the MC. Record the value in
  PHASE_LOG (G0.c) and proceed to C3.
- **FAIL, prefetch-issued == 0**: the L2 prefetcher issued nothing — a wiring bug
  in `run_se.py` (prefetch_on_access / wrong cache), NOT a flag drop. Fix wiring
  (old C5), rebuild, re-run this gate.
- **FAIL, prefetch-issued > 0 but `prefetchEnqueued == 0`**: the flag is lost en
  route. STOP. Diagnose per FIX-3 step 3 (LLC miss path vs membus vs trafficgen
  tagging), patch flag forwarding minimally, rebuild, re-run this gate.
Do not proceed while either FAIL holds.

### C3 — Task 3 Step 3: B0 config elaborates and runs
```bash
./gem5/build/X86/gem5.opt --outdir=m5out/b0_smoke \
  gem5/configs/dprh/run_se.py --config B0 \
  --cmd gem5/tests/test-progs/hello/bin/x86/linux/hello \
  --ff-offset 0 --warmup 0 --measure 1000000
```
Expected: elaborates with no `AttributeError`; `m5out/b0_smoke/stats.txt` contains
`system.mem_ctrl` stats and a nonzero `simInsts`.

### C4 — Task 4 Step 1: B1 with SPP
```bash
./gem5/build/X86/gem5.opt --outdir=m5out/b1_smoke \
  gem5/configs/dprh/run_se.py --config B1 --prefetcher spp \
  --cmd gem5/tests/test-progs/hello/bin/x86/linux/hello \
  --ff-offset 0 --warmup 0 --measure 5000000 2>&1 | tail -3
```

### C5 — Task 4 Step 2: Assert prefetcher issued prefetches
```bash
grep -iE "prefetch.*(issued|Issued|num_hwpf|pfIssued)" m5out/b1_smoke/stats.txt | head
```
Expected: a nonzero prefetch-issued stat under `system.cpu.l2cache`. Record the
exact stat name in PHASE_LOG. If zero: fix wiring in run_se.py (prefetch_on_access,
correct cache) before continuing — V1 cannot pass without prefetches.

### C6 — V1 (MOVED to the front — see C2·V1)
FIX-3 moved V1 to the first data-producing slot (C2·V1) and replaced the old
TEMP-probe rebuild with the permanent `prefetchEnqueued` counter, so no probe
re-apply is needed here. This slot is retained only as a back-reference: V1 must
already have PASSED at C2·V1 before you reached this point. For an optional
per-packet trace, add `--debug-flags=MemCtrl` to the C2·V1 run.

### C7 — Task 6 Step 6: Prove default-off == stock
```bash
./scripts/build_gem5.sh
./gem5/build/X86/gem5.opt --outdir=m5out/b1_after6 gem5/configs/dprh/run_se.py \
  --config B1 --prefetcher spp --cmd gem5/tests/test-progs/hello/bin/x86/linux/hello \
  --ff-offset 0 --warmup 0 --measure 5000000
diff <(grep -E "simInsts|system.mem_ctrl.*(readReqs|bytesRead)" m5out/b1_smoke/stats.txt) \
     <(grep -E "simInsts|system.mem_ctrl.*(readReqs|bytesRead)" m5out/b1_after6/stats.txt)
```
Expected: **no diff** (disabled seam changed nothing; B1 does not set enable_dprh).

### C8 — Task 7 Step 5: Filter unit test
```bash
cd gem5 && scons build/X86/unittests.opt && \
  ./build/X86/mem/dprh_filter.test.opt 2>&1 | tail -5
```
Expected: `[  PASSED  ] 2 tests.`
(NOTE: exact test binary path may be `build/X86/mem/dprh_filter.test.opt`; if scons
reports a different output path, use that. See Task 7 note.)

### C9 — Task 7 Step 8: B1 with filter, confirm stat present
```bash
./scripts/build_gem5.sh && ./gem5/build/X86/gem5.opt --outdir=m5out/b1_filter \
  gem5/configs/dprh/run_se.py --config B1 --prefetcher spp \
  --cmd gem5/tests/test-progs/hello/bin/x86/linux/hello --ff-offset 0 --warmup 0 --measure 5000000
grep filterDroppedPrefetches m5out/b1_filter/stats.txt
```
Expected: run completes; `filterDroppedPrefetches` present (0 is fine under optimistic warm-up).

### C10 — Task 8 Step 3: B2 differs from B1
```bash
./scripts/build_gem5.sh
for cfg in B1 B2; do ./gem5/build/X86/gem5.opt --outdir=m5out/${cfg}_cmp \
  gem5/configs/dprh/run_se.py --config ${cfg} --prefetcher spp \
  --cmd gem5/tests/test-progs/hello/bin/x86/linux/hello --ff-offset 0 --warmup 0 --measure 20000000; done
grep -E "system.mem_ctrl.*avgRdQLen|readRowHitRate|avgMemAccLat" m5out/B1_cmp/stats.txt m5out/B2_cmp/stats.txt
```
Expected: at least one MC scheduling stat differs between B1 and B2 (delta may be
tiny on hello; real B2!=B1 check is on a memory-intensive trace in Task 12/G0).

### C11 — Task 9 Step 4: All Phase 1 stats appear
```bash
./scripts/build_gem5.sh && ./gem5/build/X86/gem5.opt --outdir=m5out/stats \
  gem5/configs/dprh/run_se.py --config B2 --prefetcher spp \
  --cmd gem5/tests/test-progs/hello/bin/x86/linux/hello --ff-offset 0 --warmup 0 --measure 20000000
for s in schedCycles cyclesNoLegalDemand cyclesHslot demandReadLatency prefetchReadLatency nonHslotReason; do
  grep -q "$s" m5out/stats/stats.txt && echo "OK $s" || echo "MISSING $s"; done
```
Expected: all `OK`.

### C12 — Task 10 Step 4: Traffic-gen row-locality calibration sweep
```bash
for n in 1 4 16; do ./gem5/build/X86/gem5.opt --outdir=m5out/cal_$n \
  gem5/configs/dprh/run_trafficgen.py --demand-period 1000 --pf-seq-pkts $n --pf-tag; done
python3 scripts/analyze_calibration.py m5out/cal_1 m5out/cal_4 m5out/cal_16
```
Expected: PASS — DRAM row-hit rate strictly increases with `num_seq_pkts`.

### C13 — Task 10 Step 5: Confirm prefetch tag seen at MC (synthetic)
```bash
grep -E "prefetchReadLatency::mean|prefetchReadLatency::samples" m5out/cal_16/stats.txt
```
Expected: `samples > 0`.

### C14 — Task 11 Step 1: Cross-compile SPEC for SE mode (see benchmarks/README.md)
Build static x86 SE-mode SPEC binaries per benchmarks/README.md; record the
exclusion list there. No single command — follow benchmarks/README.md.

### C15 — Task 11 Step 3: MPKI profiling pass
```bash
python3 scripts/mpki_profile.py --benchmarks-dir benchmarks/ \
  --measure 50000000 --out results/mpki_profile.csv 2>&1 | tee results/mpki.log
```
Expected: results/mpki_profile.csv lists each benchmark LLC MPKI; ~10–15 land >= 1.
Then freeze R2 (MPKI>=1), R3 controls (MPKI<0.5), R5 held-out in PHASE_LOG.

### C16 — Task 12 Step 2: 3-trace smoke test
```bash
./scripts/smoke_test.sh <bench1> <bench2> <bench3> 2>&1 | tee results/smoke.log
```
Then verify (Task 12 Step 3) from results/smoke.csv: (a) B1 IPC > B0 IPC on
memory-intensive traces; (b) B2 differs from B1 on >=1 trace; (c) no NaN/zero IPC.

### C17 — Task 14: Evaluate Gate G0
Using results/smoke.csv, tests/dprh/test_v1_prefetch_flag.md, and the calibration
output, evaluate G0.a–G0.d and sign off in results/PHASE_LOG.md. See Task 14.
DO NOT mark G0 PASSED until all four conditions are observed on the cluster.

---

## Phase 1 — Characterization Handoff (P-series)

> **DO NOT RUN UNTIL G0 IS PASSED.** All P-series commands are gated behind Gate
> G0 in `results/PHASE_LOG.md`. If G0 has not been signed (all four conditions
> G0.a–G0.d confirmed on the cluster), stop here and return to C17.

Note on sync: pull both repos before starting.
```bash
cd ~/DPRH        && git pull ~/dprh-outer.bundle v0
cd ~/DPRH/gem5   && git pull ~/dprh-gem5.bundle stable
```
(Or re-rsync if using Option B from C0.)

---

### P1 — Build Phase-1 gem5.opt + unit tests

Build the Phase-1 instrumented binary and verify all DPRH unit tests pass,
including the new `dprh_late.test` and the extended `dprh_hslot.test`.

```bash
cd gem5
scons build/X86/gem5.opt -j$(nproc) 2>&1 | tee ../results/build_phase1.log
scons build/X86/unittests.opt 2>&1 | tee -a ../results/build_phase1.log
```

Expected:
- `build/X86/gem5.opt` links cleanly.
- All `dprh_*.test` binaries build and pass:
  ```bash
  for t in dprh_hslot dprh_filter dprh_demand_first dprh_late; do
    echo "--- $t ---"
    ./build/X86/mem/${t}.test.opt
  done
  ```
  Each must print `[  PASSED  ] N test(s).` with 0 failures. The
  `dprh_late.test` (4 tests) and `dprh_hslot.test` (now 4 tests, including
  the new aged-blocked case) are new in Phase 1.

After success: record the build host and log tail in `results/PHASE_LOG.md`.

---

### P1b — Instruction-window smoke (guards the run_se warmup/measure fix)

Fast regression guard for the run_se.py warmup/measure windowing (gem5
`d2e6076`). Before that fix, the O3 `--measure` window did not exist (the
post-instantiate `max_insts_any_thread` assignment was a no-op), so every
measured H_slot number would have come from the wrong instruction window. This
smoke confirms the measure phase runs the intended instruction count.

Use a **long-running workload** (a SPEC binary — the tiny `hello` test-prog exits
long before the window completes and would fail spuriously). Small windows keep it
to a few minutes:

```bash
cd ~/DPRH
gem5/build/X86/gem5.opt --outdir=results/runs/instwin \
  gem5/configs/dprh/run_se.py --config B1 --prefetcher spp \
  --cmd <one R2 SPEC binary> --options "<its args>" \
  --ff-offset 1000000 --warmup 200000 --measure 300000
python3 scripts/check_inst_window.py results/runs/instwin --expect 300000
```

Expected: `simInsts = ~300000 ... -> PASS`. A FAIL reporting ~0 (or the whole
program) means the `scheduleInstStop` wiring regressed — fix `run_se.py` before
P2/P3. `check_inst_window.py --selftest` runs on any machine (no gem5) and is the
CI-side unit check for the tolerance logic.

---

### P2 — Synthetic 3×3 H_slot attribution factorial (RUN BEFORE P3)

Drive the R6 synthetic factorial **before** the real-trace sweep; it is fast and
validates the Phase-1 instrumentation before committing cluster CPU time to SPEC.

```bash
cd ~/DPRH
bash scripts/hslot_factorial.sh gem5/build/X86/gem5.opt 64
```

This runs 18 gem5 invocations (B1 × B2 × 3 demand periods × 3 seq-pkts levels)
with `--a-guard 64`, writing one outdir per cell under `results/runs/factorial/`.

Then analyze and check attribution monotonicity:

```bash
python3 scripts/analyze_hslot_surface.py results/runs/factorial \
        --out results/phase1_surface.csv
```

Expected: prints `ATTRIBUTION: PASS` (H_slot is non-decreasing in both demand
pressure and prefetch row-locality dimensions). If FAIL, diagnose the anomalous
cell before proceeding to P3 (a monotonicity failure may indicate a
instrumentation bug or traffic-gen misconfiguration).

After PASS: commit the surface CSV:
```bash
git add results/phase1_surface.csv
git commit -m "dprh(phase1): factorial surface CSV (cluster run)"
```

---

### P3 — Real-trace B1/B2 sweep with Phase-1 instrumentation

For each workload in the R2 memory-intensive set (from `results/mpki_profile.csv`,
MPKI ≥ 1) and each config in `{B1, B2}`, run:

```bash
./gem5/build/X86/gem5.opt \
  --outdir results/runs/<workload>/<config> \
  gem5/configs/dprh/run_se.py \
  --config <config> \
  --prefetcher spp \
  --a-guard 64 \
  --cmd <path/to/binary> \
  --options "<benchmark arguments>" \
  --ff-offset <N> --warmup <W> --measure 50000000
```

Route held-out traces (from `results/HELD_OUT.md`) through `dispatch_guard.py`;
only use `--final-run` for the final authoritative held-out measurement:

```bash
python3 scripts/dispatch_guard.py --check <workload> || \
  python3 scripts/dispatch_guard.py --check <workload> --final-run
```

After each run, verify that `system.mem_ctrl.demandReadsSeen` is non-zero and
that at least one memory-intensive workload produces non-zero
`system.mem_ctrl.agedDemandBlocked` and
`system.mem_ctrl.nonHslotReason::aged_demand` with `--a-guard 64`:

```bash
grep -E "demandReadsSeen|agedDemandBlocked|nonHslotReason::aged_demand" \
     results/runs/<workload>/B2/stats.txt
```

If both `agedDemandBlocked` and `nonHslotReason::aged_demand` are zero on ALL
memory-intensive traces, the aged-demand bin is not firing — diagnose Task 5
population site in `mem_ctrl.cc` before proceeding to P4.

---

### P4 — Aggregate + Gate G1

Aggregate the P3 runs into the per-workload H_slot table:

```bash
python3 scripts/aggregate_hslot.py results/runs \
        --out results/phase1_hslot.csv
```

Record the printed `pressure threshold (B2 bus_util%):` value in
`results/PHASE_LOG.md` (the pre-registered R8 threshold line) **before**
inspecting any DPRH-relevant delta.

Then evaluate Gate G1:

```bash
python3 scripts/gate_g1.py results/phase1_hslot.csv \
        --controls-file results/r3_controls.txt
```

(`results/r3_controls.txt` is one workload name per line for the R3 no-harm
control set; fill from `results/mpki_profile.csv`, MPKI < 0.5.)

The script prints the per-workload B2 H_slot fractions, the median, and a
verdict tag (`PIVOT` / `PROCEED_TIMELINESS` / `PROCEED_PERFORMANCE`). It does
**not** sign Gate G1. Paste the output block into `results/PHASE_LOG.md`'s Gate
G1 scaffold (the `Measured` field) and then **a human signs G1** by committing:

```bash
git add results/phase1_hslot.csv results/PHASE_LOG.md
git commit -m "dprh(phase1): Gate G1 evaluated — <PIVOT|PROCEED_TIMELINESS|PROCEED_PERFORMANCE>"
```

Do NOT begin Phase 2 until this commit exists.
