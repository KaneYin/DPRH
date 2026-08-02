#!/usr/bin/env python3
"""DPRH Phase 1: build the H_slot attribution surface from the R6 factorial.

Reads the gem5 outdirs produced by scripts/hslot_factorial.sh, computes
H_slot = cyclesHslot / schedCycles per (config, demand_period, seq_pkts) cell,
and emits results/phase1_surface.csv. Also asserts the two R6 attribution
directions the committee will probe:
  (A1) at fixed demand pressure, H_slot is non-decreasing in prefetch
       row-locality density (num_seq_pkts);
  (A2) at fixed row-locality, H_slot is non-decreasing as demand pressure falls
       (period rises).

Cell dir names are '<config>_p<period>_s<seq>' (see hslot_factorial.sh).

Usage:
    analyze_hslot_surface.py results/runs/factorial [--out results/phase1_surface.csv]
    analyze_hslot_surface.py --selftest
"""
import argparse
import csv
import os
import re
import sys

SCHED = "system.mem_ctrl.schedCycles"
HSLOT = "system.mem_ctrl.cyclesHslot"
CELL_RE = re.compile(r"^(B0|B1|B2|DPRH)_p(\d+)_s(\d+)$")


def parse_stat(stats_path, name):
    if not os.path.isfile(stats_path):
        return None
    with open(stats_path) as f:
        for line in f:
            m = re.match(r"^" + re.escape(name) + r"\s+([-\d.eE+]+)", line)
            if m:
                return float(m.group(1))
    return None


def collect(root):
    """Return list of dicts: config, period, seq, hslot_frac."""
    rows = []
    for name in sorted(os.listdir(root)):
        m = CELL_RE.match(name)
        if not m:
            continue
        stats = os.path.join(root, name, "stats.txt")
        sched = parse_stat(stats, SCHED)
        hslot = parse_stat(stats, HSLOT)
        frac = (hslot / sched) if (sched and sched > 0) else None
        rows.append({
            "config": m.group(1),
            "period": int(m.group(2)),
            "seq": int(m.group(3)),
            "hslot_frac": frac,
        })
    return rows


def _nondecreasing(pairs):
    """pairs: list of (x, y) sorted by x; True if y is non-decreasing in x."""
    ys = [y for _, y in sorted(pairs)]
    return all(b >= a for a, b in zip(ys, ys[1:]))


def check_attribution(rows):
    """Return (ok, messages). Applies A1/A2 per config, ignoring None cells."""
    ok, msgs = True, []
    by_cfg = {}
    for r in rows:
        by_cfg.setdefault(r["config"], []).append(r)
    for cfg, rs in sorted(by_cfg.items()):
        # A1: fix period, vary seq.
        for per in sorted({r["period"] for r in rs}):
            pts = [(r["seq"], r["hslot_frac"]) for r in rs
                   if r["period"] == per and r["hslot_frac"] is not None]
            if len(pts) >= 2 and not _nondecreasing(pts):
                ok = False
                msgs.append(f"A1 FAIL {cfg} period={per}: H_slot not "
                            f"non-decreasing in seq: {sorted(pts)}")
        # A2: fix seq, vary period (rising period = falling pressure).
        for seq in sorted({r["seq"] for r in rs}):
            pts = [(r["period"], r["hslot_frac"]) for r in rs
                   if r["seq"] == seq and r["hslot_frac"] is not None]
            if len(pts) >= 2 and not _nondecreasing(pts):
                ok = False
                msgs.append(f"A2 FAIL {cfg} seq={seq}: H_slot not "
                            f"non-decreasing as pressure falls: {sorted(pts)}")
    return ok, msgs


def write_csv(rows, out):
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["config", "period", "seq",
                                          "hslot_frac"])
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["config"], r["period"],
                                             r["seq"])):
            w.writerow(r)


def selftest():
    # Synthetic rows: H_slot rises with seq and with period -> A1/A2 pass.
    rows = []
    for cfg in ("B1", "B2"):
        for pi, per in enumerate((250, 1000, 4000)):
            for si, seq in enumerate((1, 4, 16)):
                rows.append({"config": cfg, "period": per, "seq": seq,
                             "hslot_frac": 0.01 * (pi + 1) + 0.02 * (si + 1)})
    ok, msgs = check_attribution(rows)
    assert ok, msgs
    # Break A1 in one cell -> must FAIL.
    rows[1]["hslot_frac"] = 999.0  # B1 period=250 seq=4 spike then drop
    bad, _ = check_attribution(rows)
    assert not bad, "selftest: expected A1 violation to be detected"
    print("analyze_hslot_surface selftest: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", nargs="?", help="results/runs/factorial")
    ap.add_argument("--out", default="results/phase1_surface.csv")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.root:
        ap.error("root is required unless --selftest")
    rows = collect(args.root)
    if not rows:
        print(f"FAIL: no factorial cells found under {args.root}")
        return 2
    write_csv(rows, args.out)
    missing = [r for r in rows if r["hslot_frac"] is None]
    for r in sorted(rows, key=lambda r: (r["config"], r["period"], r["seq"])):
        shown = "MISSING" if r["hslot_frac"] is None else f"{r['hslot_frac']:.4f}"
        print(f"  {r['config']:>4} period={r['period']:>5} seq={r['seq']:>3} "
              f"-> H_slot={shown}")
    print(f"\nwrote {args.out}")
    if missing:
        print(f"FAIL: {len(missing)} cell(s) missing schedCycles/cyclesHslot.")
        return 2
    ok, msgs = check_attribution(rows)
    for m in msgs:
        print(m)
    print("\nATTRIBUTION: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
