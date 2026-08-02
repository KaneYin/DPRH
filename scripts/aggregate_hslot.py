#!/usr/bin/env python3
"""DPRH Phase 1: per-workload H_slot aggregation across the R2 suite (R8).

For each workload, reads the B1 and B2 gem5 outdirs and emits one CSV row per
(workload, config) with:
  - hslot_frac              = cyclesHslot / schedCycles
  - proxy_frac              = cyclesReadyPrefetchNoDemand / schedCycles
  - reason_<bin>_frac       = nonHslotReason::<bin> / schedCycles (5 bins)
  - aged_blocked_frac       = agedDemandBlocked / schedCycles
  - late_prefetch_rate      = latePrefetchDemands / demandReadsSeen
  - demand_rowhit_rate      = demandRowHits / demandReadLatency::samples
  - prefetch_rowhit_rate    = prefetchRowHits / prefetchReadLatency::samples
  - bus_util                = system.mem_ctrl.dram.busUtil (bandwidth pressure)
  - pressure_bin            = 'high' if the workload's B2 bus_util >= threshold

R8: results are per-workload (never averages alone) and binned by bandwidth
pressure. The threshold is pre-registered: the median B2 bus_util across the
suite (printed and written to the CSV header comment) unless --pressure-thresh
is given.

Layout expected (one dir per workload/config):
    <runs>/<workload>/<config>/stats.txt      e.g. runs/lbm/B2/stats.txt

Usage:
    aggregate_hslot.py <runs-dir> [--out results/phase1_hslot.csv]
                       [--pressure-thresh PCT]
    aggregate_hslot.py --selftest
"""
import argparse
import csv
import os
import re
import statistics
import sys

REASON_BINS = ["demand_ready", "no_prefetch", "pf_not_rowhit",
               "turnaround_unsafe", "aged_demand"]


def parse_stats(stats_path):
    """Return {stat_name: float} for every scalar line in stats.txt."""
    out = {}
    if not os.path.isfile(stats_path):
        return out
    with open(stats_path) as f:
        for line in f:
            m = re.match(r"^(\S+)\s+([-\d.eE+]+)", line)
            if m:
                try:
                    out[m.group(1)] = float(m.group(2))
                except ValueError:
                    pass
    return out


def _safe_div(num, den):
    return (num / den) if (num is not None and den and den > 0) else None


def workload_config_row(workload, config, s):
    """Build one metrics row from a parsed stats dict `s`."""
    P = "system.mem_ctrl."
    sched = s.get(P + "schedCycles")
    row = {"workload": workload, "config": config}
    row["hslot_frac"] = _safe_div(s.get(P + "cyclesHslot"), sched)
    row["proxy_frac"] = _safe_div(s.get(P + "cyclesReadyPrefetchNoDemand"),
                                  sched)
    for b in REASON_BINS:
        row[f"reason_{b}_frac"] = _safe_div(
            s.get(P + "nonHslotReason::" + b), sched)
    row["aged_blocked_frac"] = _safe_div(s.get(P + "agedDemandBlocked"), sched)
    row["late_prefetch_rate"] = _safe_div(
        s.get(P + "latePrefetchDemands"), s.get(P + "demandReadsSeen"))
    row["demand_rowhit_rate"] = _safe_div(
        s.get(P + "demandRowHits"), s.get(P + "demandReadLatency::samples"))
    row["prefetch_rowhit_rate"] = _safe_div(
        s.get(P + "prefetchRowHits"), s.get(P + "prefetchReadLatency::samples"))
    row["bus_util"] = s.get(P + "dram.busUtil")
    return row


def collect(runs_dir):
    rows = []
    for workload in sorted(os.listdir(runs_dir)):
        wdir = os.path.join(runs_dir, workload)
        if not os.path.isdir(wdir):
            continue
        for config in ("B1", "B2"):
            stats = os.path.join(wdir, config, "stats.txt")
            if os.path.isfile(stats):
                rows.append(workload_config_row(
                    workload, config, parse_stats(stats)))
    return rows


def assign_pressure_bins(rows, thresh):
    """Mutate rows: pressure_bin from each workload's B2 bus_util vs thresh.

    Returns the threshold actually used (median B2 bus_util if thresh is None).
    """
    b2 = {r["workload"]: r["bus_util"] for r in rows
          if r["config"] == "B2" and r["bus_util"] is not None}
    if thresh is None:
        vals = sorted(b2.values())
        thresh = statistics.median(vals) if vals else 0.0
    for r in rows:
        u = b2.get(r["workload"])
        r["pressure_bin"] = ("high" if (u is not None and u >= thresh)
                             else "low" if u is not None else "unknown")
    return thresh


def fieldnames():
    fn = ["workload", "config", "hslot_frac", "proxy_frac"]
    fn += [f"reason_{b}_frac" for b in REASON_BINS]
    fn += ["aged_blocked_frac", "late_prefetch_rate", "demand_rowhit_rate",
           "prefetch_rowhit_rate", "bus_util", "pressure_bin"]
    return fn


def write_csv(rows, out, thresh):
    with open(out, "w", newline="") as f:
        f.write(f"# pressure_thresh(bus_util%)={thresh:.3f}; "
                f"bin=high iff workload B2 bus_util>=thresh (R8)\n")
        w = csv.DictWriter(f, fieldnames=fieldnames())
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["workload"], r["config"])):
            w.writerow(r)


def selftest():
    rows = [
        workload_config_row("lbm", "B1", {
            "system.mem_ctrl.schedCycles": 1000.0,
            "system.mem_ctrl.cyclesHslot": 120.0,
            "system.mem_ctrl.latePrefetchDemands": 30.0,
            "system.mem_ctrl.demandReadsSeen": 300.0,
            "system.mem_ctrl.demandRowHits": 50.0,
            "system.mem_ctrl.demandReadLatency::samples": 200.0,
            "system.mem_ctrl.dram.busUtil": 80.0,
        }),
        workload_config_row("lbm", "B2", {
            "system.mem_ctrl.schedCycles": 1000.0,
            "system.mem_ctrl.cyclesHslot": 90.0,
            "system.mem_ctrl.dram.busUtil": 80.0,
        }),
        workload_config_row("leela", "B2", {
            "system.mem_ctrl.schedCycles": 1000.0,
            "system.mem_ctrl.cyclesHslot": 5.0,
            "system.mem_ctrl.dram.busUtil": 10.0,
        }),
    ]
    assert abs(rows[0]["hslot_frac"] - 0.12) < 1e-9
    assert abs(rows[0]["late_prefetch_rate"] - 0.10) < 1e-9
    assert abs(rows[0]["demand_rowhit_rate"] - 0.25) < 1e-9
    thresh = assign_pressure_bins(rows, None)  # median of {80, 10} = 45
    assert abs(thresh - 45.0) < 1e-9, thresh
    bins = {(r["workload"], r["config"]): r["pressure_bin"] for r in rows}
    assert bins[("lbm", "B2")] == "high"
    assert bins[("leela", "B2")] == "low"
    # Missing denominators must yield None, not a crash.
    empty = workload_config_row("x", "B1", {})
    assert empty["hslot_frac"] is None and empty["late_prefetch_rate"] is None
    print("aggregate_hslot selftest: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("runs", nargs="?", help="dir of <workload>/<config>/stats.txt")
    ap.add_argument("--out", default="results/phase1_hslot.csv")
    ap.add_argument("--pressure-thresh", type=float, default=None,
                    help="bus_util%% threshold (default: median B2 bus_util)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.runs:
        ap.error("runs is required unless --selftest")
    rows = collect(args.runs)
    if not rows:
        print(f"FAIL: no <workload>/<config>/stats.txt under {args.runs}")
        return 2
    thresh = assign_pressure_bins(rows, args.pressure_thresh)
    write_csv(rows, args.out, thresh)
    print(f"pressure threshold (B2 bus_util%): {thresh:.3f}")
    for r in sorted(rows, key=lambda r: (r["workload"], r["config"])):
        h = "NA" if r["hslot_frac"] is None else f"{100*r['hslot_frac']:.2f}%"
        print(f"  {r['workload']:>12} {r['config']:>3}  H_slot={h:>7}  "
              f"pressure={r['pressure_bin']}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
