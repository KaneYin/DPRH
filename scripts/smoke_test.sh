#!/usr/bin/env bash
set -euo pipefail
# 3-trace B0/B1/B2 smoke test (plan Task 12). [cluster] script.
#
# Usage: ./scripts/smoke_test.sh [--final-run] <bench1> <bench2> <bench3>
# where each <benchN> is a workload binary launch spec (path passed to --cmd).
# --final-run permits R5 held-out traces (FIX-4 dispatch guard); omit it for
# normal (tuning) runs, which refuse to touch held-out traces.
# Runs {B0,B1,B2} for each at a modest window, then extracts IPC, LLC MPKI, and
# DRAM read row-hit rate into results/smoke.csv.
#
# Sanity relations verified afterward from results/smoke.csv (Task 12 Step 3):
#   (a) B1 IPC > B0 IPC on memory-intensive traces (prefetch speedup exists);
#   (b) B2 differs measurably from B1 on >=1 trace (scheduler layer matters);
#   (c) no NaN/zero IPC.

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GEM5="${REPO_ROOT}/gem5/build/X86/gem5.opt"
RUN_SE="${REPO_ROOT}/gem5/configs/dprh/run_se.py"

FINAL_RUN=""
if [ "${1:-}" = "--final-run" ]; then
  FINAL_RUN="--final-run"
  shift
fi

if [ "$#" -lt 1 ]; then
  echo "usage: $0 [--final-run] <bench1> [<bench2> <bench3> ...]" >&2
  exit 2
fi
if [ ! -x "${GEM5}" ]; then
  echo "ERROR: gem5.opt not found at ${GEM5}; build on the cluster first." >&2
  exit 2
fi

BENCHES=("$@")

# FIX-4: refuse to run R5 held-out traces unless --final-run was passed.
python3 "${REPO_ROOT}/scripts/dispatch_guard.py" --check "${BENCHES[@]}" \
  ${FINAL_RUN:+$FINAL_RUN} || exit $?
for b in "${BENCHES[@]}"; do
  for cfg in B0 B1 B2; do
    "${GEM5}" --outdir="${REPO_ROOT}/m5out/smoke_${b##*/}_${cfg}" \
      "${RUN_SE}" --config "${cfg}" --prefetcher spp \
      --cmd "${b}" --ff-offset 1000000000 --warmup 10000000 --measure 50000000
  done
done

python3 - "$REPO_ROOT" "${BENCHES[@]}" <<'PY'
import csv, os, re, sys
repo_root = sys.argv[1]
benches = sys.argv[2:]

def stat(text, names):
    for n in names:
        m = re.search(r"^" + re.escape(n) + r"\s+([-\d.eEnNaA+]+)", text, re.M)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None

IPC   = ["system.o3.ipc", "system.switch_cpus.ipc", "system.cpu.ipc"]
INSTS = ["simInsts", "system.o3.numInsts"]
TICKS = ["simTicks"]
MISS  = ["system.llc.demandMisses::total", "system.llc.overallMisses::total"]
ROWHIT = ["system.mem_ctrl.dram.readRowHitRate",
          "system.mem_ctrl.dram.pageHitRate"]

rows = []
for b in benches:
    name = os.path.basename(b)
    for cfg in ("B0", "B1", "B2"):
        outdir = os.path.join(repo_root, "m5out", f"smoke_{name}_{cfg}")
        sp = os.path.join(outdir, "stats.txt")
        if not os.path.isfile(sp):
            rows.append(dict(bench=name, cfg=cfg, ipc=None, mpki=None,
                             row_hit_rate=None, note="no stats.txt"))
            continue
        text = open(sp).read()
        ipc = stat(text, IPC)
        insts = stat(text, INSTS)
        ticks = stat(text, TICKS)
        if ipc is None and insts and ticks:
            # IPC ~ insts / (ticks/cycle). Fallback proxy only.
            ipc = None
        misses = stat(text, MISS)
        mpki = (misses / (insts / 1000.0)) if (misses and insts) else None
        rows.append(dict(bench=name, cfg=cfg, ipc=ipc, mpki=mpki,
                         row_hit_rate=stat(text, ROWHIT), note=""))

out = os.path.join(repo_root, "results", "smoke.csv")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["bench", "cfg", "ipc", "mpki",
                                      "row_hit_rate", "note"])
    w.writeheader()
    for r in rows:
        w.writerow(r)
print(f"[smoke] wrote {out}")

# Sanity summary (informational; final PASS/FAIL is the operator's per Task 12/14)
by = {}
for r in rows:
    by[(r["bench"], r["cfg"])] = r
for b in {r["bench"] for r in rows}:
    b0 = by.get((b, "B0"), {})
    b1 = by.get((b, "B1"), {})
    b2 = by.get((b, "B2"), {})
    print(f"[smoke] {b}: B0.ipc={b0.get('ipc')} B1.ipc={b1.get('ipc')} "
          f"B2.ipc={b2.get('ipc')} | B1.rowhit={b1.get('row_hit_rate')} "
          f"B2.rowhit={b2.get('row_hit_rate')}")
PY
