#!/usr/bin/env python3
"""MPKI profiling driver + R2 suite selection.

Drives gem5/configs/dprh/run_se.py --config B1 over every runnable SPEC SE-mode
benchmark for a fixed-offset measurement window, parses each stats.txt for LLC
misses and committed instructions, computes

    LLC_MPKI = LLC_demand_misses / (simInsts / 1000)

and writes results/mpki_profile.csv plus the selected suites:
  - R2 main set:      per-benchmark LLC MPKI >= 1
  - R3 no-harm ctrls: LLC MPKI < 0.5
  - (R5 held-out is chosen by the user from the R2 set and recorded in
     PHASE_LOG.md, not auto-selected here.)

This script is [cluster]-run (it invokes gem5.opt). On macOS it is authored and
syntax-checked only; run it on the Linux cluster after SPEC is cross-compiled.

Benchmark discovery: --benchmarks-dir is scanned for a manifest file
`benchmarks.tsv` with lines: "<name>\t<binary_path>\t<args>". If absent, every
executable directly under --benchmarks-dir is treated as a benchmark with no
args. Excluded benchmarks (SE-mode-incompatible) belong in benchmarks/README.md
and should simply be omitted from the manifest.

LLC cache stat path: system.llc.demandMisses::total (run_se.py names the LLC
`system.llc`). Falls back to overallMisses::total, then any *.llc.*Misses total.
"""
import argparse
import csv
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEM5 = os.path.join(REPO_ROOT, "gem5", "build", "X86", "gem5.opt")
RUN_SE = os.path.join(REPO_ROOT, "gem5", "configs", "dprh", "run_se.py")

LLC_MISS_STATS = [
    "system.llc.demandMisses::total",
    "system.llc.overallMisses::total",
]
INSTS_STATS = ["simInsts", "system.o3.numInsts", "system.o3.committedInsts"]


def parse_stat(text, names):
    for name in names:
        m = re.search(
            r"^" + re.escape(name) + r"\s+([-\d.eE+]+)", text, re.MULTILINE
        )
        if m:
            return float(m.group(1))
    return None


def discover_benchmarks(bench_dir):
    manifest = os.path.join(bench_dir, "benchmarks.tsv")
    benches = []
    if os.path.isfile(manifest):
        with open(manifest) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                name = parts[0]
                binary = parts[1] if len(parts) > 1 else ""
                bargs = parts[2] if len(parts) > 2 else ""
                benches.append((name, binary, bargs))
    else:
        for entry in sorted(os.listdir(bench_dir)):
            path = os.path.join(bench_dir, entry)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                benches.append((entry, path, ""))
    return benches


def run_one(name, binary, bargs, measure, ff_offset, outroot):
    outdir = os.path.join(outroot, f"mpki_{name}")
    cmd = [
        GEM5, f"--outdir={outdir}", RUN_SE,
        "--config", "B1", "--prefetcher", "spp",
        "--cmd", binary,
        "--ff-offset", str(ff_offset),
        "--warmup", "0",
        "--measure", str(measure),
    ]
    if bargs:
        cmd += ["--options", bargs]
    print(f"[mpki] running {name}: {' '.join(cmd)}")
    subprocess.run(cmd, check=False)
    stats_path = os.path.join(outdir, "stats.txt")
    if not os.path.isfile(stats_path):
        return None, None
    with open(stats_path) as f:
        text = f.read()
    misses = parse_stat(text, LLC_MISS_STATS)
    insts = parse_stat(text, INSTS_STATS)
    return misses, insts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--benchmarks-dir", required=True)
    ap.add_argument("--measure", type=int, default=50_000_000)
    ap.add_argument("--ff-offset", type=int, default=1_000_000_000)
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "results",
                                                  "mpki_profile.csv"))
    ap.add_argument("--outroot", default=os.path.join(REPO_ROOT, "m5out"))
    ap.add_argument("--r2-threshold", type=float, default=1.0)
    ap.add_argument("--r3-threshold", type=float, default=0.5)
    args = ap.parse_args()

    if not os.path.isfile(GEM5):
        print(f"[mpki] ERROR: gem5.opt not found at {GEM5} -- build on the "
              f"cluster first (scripts/build_gem5.sh).", file=sys.stderr)
        return 2

    benches = discover_benchmarks(args.benchmarks_dir)
    if not benches:
        print(f"[mpki] ERROR: no benchmarks found under "
              f"{args.benchmarks_dir}", file=sys.stderr)
        return 2

    results = []
    for name, binary, bargs in benches:
        misses, insts = run_one(name, binary, bargs, args.measure,
                                 args.ff_offset, args.outroot)
        if misses is None or insts is None or insts <= 0:
            mpki = None
        else:
            mpki = misses / (insts / 1000.0)
        results.append({
            "benchmark": name, "llc_misses": misses,
            "sim_insts": insts, "llc_mpki": mpki,
        })

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["benchmark", "llc_misses", "sim_insts", "llc_mpki",
                        "class"],
        )
        w.writeheader()
        r2, r3 = [], []
        for r in results:
            mpki = r["llc_mpki"]
            if mpki is None:
                cls = "ERROR"
            elif mpki >= args.r2_threshold:
                cls = "R2_main"
                r2.append(r["benchmark"])
            elif mpki < args.r3_threshold:
                cls = "R3_control"
                r3.append(r["benchmark"])
            else:
                cls = "middle"
            r["class"] = cls
            w.writerow(r)

    print(f"\n[mpki] wrote {args.out}")
    print(f"[mpki] R2 main set (MPKI >= {args.r2_threshold}): {r2}")
    print(f"[mpki] R3 no-harm controls (MPKI < {args.r3_threshold}): {r3}")
    print("[mpki] Choose 2-3 R5 held-out traces from the R2 set and record "
          "R2/R3/R5 in results/PHASE_LOG.md (Task 11 Step 4).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
