#!/usr/bin/env python3
"""Verify the traffic-gen row-locality knob is real and strictly monotonic.

Reads a set of gem5 stats.txt files produced by a --pf-seq-pkts sweep (via
gem5/configs/dprh/run_trafficgen.py) and asserts that the measured DRAM read
row-hit rate rises strictly with num_seq_pkts. Prints a
(seq_pkts -> row_hit_rate) table and a PASS/FAIL.

Usage:
    analyze_calibration.py <outdir1> <outdir2> ... [--stat NAME]
    analyze_calibration.py --selftest

Each <outdir> is a gem5 --outdir containing stats.txt. The num_seq_pkts value
is inferred from the directory name (expects a trailing integer, e.g. cal_16),
falling back to the file order if it cannot be parsed.

Row-hit-rate stat: system.mem_ctrl.dram.readRowHitRate (gem5 DRAMInterface).
pageHitRate is used as a fallback if readRowHitRate is absent.
"""
import argparse
import os
import re
import sys

DEFAULT_STATS = [
    "system.mem_ctrl.dram.readRowHitRate",
    "system.mem_ctrl.dram.pageHitRate",
]


def parse_stat(stats_path, stat_names):
    """Return the first matching stat value found in stats_path, or None."""
    if not os.path.isfile(stats_path):
        return None
    with open(stats_path) as f:
        text = f.read()
    for name in stat_names:
        # stats.txt lines look like: "<name>   <value>   # desc"
        m = re.search(
            r"^" + re.escape(name) + r"\s+([-\d.eE+]+)", text, re.MULTILINE
        )
        if m:
            return float(m.group(1))
    return None


def infer_seq_pkts(outdir, fallback):
    m = re.search(r"(\d+)\s*$", os.path.basename(outdir.rstrip("/")))
    return int(m.group(1)) if m else fallback


def strictly_increasing(values):
    """Return True only when every adjacent calibration level increases."""
    return len(values) >= 2 and all(
        b > a for a, b in zip(values, values[1:])
    )


def selftest():
    assert strictly_increasing([10.0, 20.0, 30.0])
    assert not strictly_increasing([])
    assert not strictly_increasing([10.0])
    assert not strictly_increasing([10.0, 10.0, 30.0])
    assert not strictly_increasing([10.0, 9.0, 30.0])
    print("analyze_calibration selftest: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("outdirs", nargs="*", help="gem5 outdirs with stats.txt")
    ap.add_argument(
        "--stat",
        default=None,
        help="override stat name (default tries readRowHitRate then pageHitRate)",
    )
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if len(args.outdirs) < 3:
        ap.error("at least three gem5 outdirs are required")

    stat_names = [args.stat] if args.stat else DEFAULT_STATS

    rows = []  # (seq_pkts, row_hit_rate, outdir)
    for i, outdir in enumerate(args.outdirs):
        stats_path = os.path.join(outdir, "stats.txt")
        val = parse_stat(stats_path, stat_names)
        seq = infer_seq_pkts(outdir, i)
        rows.append((seq, val, outdir))

    rows.sort(key=lambda r: r[0])

    print("num_seq_pkts -> read row-hit rate")
    print("---------------------------------")
    missing = False
    for seq, val, outdir in rows:
        shown = "MISSING" if val is None else f"{val:.4f}"
        print(f"  {seq:>6} -> {shown:>10}   ({outdir})")
        if val is None:
            missing = True

    if missing:
        print("\nFAIL: one or more stats.txt were missing the row-hit stat.")
        print("Tried:", ", ".join(stat_names))
        return 2

    vals = [v for _, v, _ in rows]
    monotonic = all(b >= a for a, b in zip(vals, vals[1:]))
    strictly = strictly_increasing(vals)

    print()
    if strictly:
        print("PASS: read row-hit rate STRICTLY increases with num_seq_pkts.")
        return 0
    if monotonic:
        print("FAIL: row-hit rate is non-decreasing but not STRICTLY "
              "increasing.")
        print("A flat step means two authored locality levels are not "
              "experimentally distinguishable; recalibrate before G0.")
        return 1
    print("FAIL: row-hit rate is NOT monotonic in num_seq_pkts -- the knob is "
          "not behaving as a row-locality control.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
