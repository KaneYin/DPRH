#!/usr/bin/env python3
"""DPRH Gate G1 evaluator (research_plan.md §5 pivot rule).

Reads results/phase1_hslot.csv (from aggregate_hslot.py), takes the B2 H_slot
fractions on the MEMORY-INTENSIVE set (all workloads NOT listed in the R3
no-harm controls), and reports the pivot verdict:

    median B2 H_slot < 2%     -> PIVOT (pure characterization; O3)
    2% <= median < 8%         -> PROCEED, timeliness/bandwidth framing first
    median >= 8%              -> PROCEED, performance framing

The R3 no-harm controls list is passed via --controls (comma-separated) or a
--controls-file with one workload per line. This script PRINTS a verdict; it
never edits PHASE_LOG.md and never "passes" the gate on its own (that is a
human commit after cluster data exists and Gate G0 has PASSED).

Usage:
    gate_g1.py results/phase1_hslot.csv --controls perlbench,leela,exchange2
    gate_g1.py --selftest
"""
import argparse
import csv
import statistics
import sys

LOW = 0.02   # < 2% -> pivot
HIGH = 0.08  # >= 8% -> performance framing


def load_b2_memintensive(csv_path, controls):
    """Return {workload: hslot_frac} for B2 rows not in the controls set."""
    out = {}
    with open(csv_path) as f:
        reader = csv.DictReader(r for r in f if not r.startswith("#"))
        for row in reader:
            if row["config"] != "B2":
                continue
            if row["workload"] in controls:
                continue
            v = row.get("hslot_frac")
            if v in (None, "", "None"):
                continue
            try:
                out[row["workload"]] = float(v)
            except ValueError:
                sys.stderr.write(
                    f"gate_g1: skipping {row['workload']} B2: "
                    f"non-numeric hslot_frac {v!r}\n")
                continue
    return out


def verdict(hslot_by_workload):
    """Return (median, tag, message)."""
    if not hslot_by_workload:
        return None, "NO_DATA", "no B2 memory-intensive H_slot values found"
    med = statistics.median(hslot_by_workload.values())
    if med < LOW:
        tag = "PIVOT"
        msg = ("median B2 H_slot < 2%: PIVOT to pure characterization + "
               "analysis of why the opportunity vanished (Outcome O3). Do NOT "
               "build MVP-0 as a performance mechanism.")
    elif med < HIGH:
        tag = "PROCEED_TIMELINESS"
        msg = ("2% <= median B2 H_slot < 8%: PROCEED to Phase 2, but frame "
               "gains as timeliness/bandwidth-efficiency first, IPC second.")
    else:
        tag = "PROCEED_PERFORMANCE"
        msg = ("median B2 H_slot >= 8%: PROCEED to Phase 2 with performance "
               "framing.")
    return med, tag, msg


def render(hslot_by_workload):
    med, tag, msg = verdict(hslot_by_workload)
    lines = ["=== Gate G1 (research_plan.md §5) ==="]
    for w, v in sorted(hslot_by_workload.items()):
        lines.append(f"  {w:>14}  B2 H_slot = {100*v:.2f}%")
    if med is not None:
        lines.append(f"\n  median B2 H_slot (mem-intensive) = {100*med:.2f}%")
    lines.append(f"  VERDICT: {tag}")
    lines.append(f"  {msg}")
    lines.append("\n  NOTE: this is advisory. Gate G1 is signed only by a human"
                 " commit to results/PHASE_LOG.md after cluster data exists and"
                 " Gate G0 has PASSED.")
    return tag, "\n".join(lines)


def selftest():
    assert verdict({"a": 0.005, "b": 0.015})[1] == "PIVOT"
    assert verdict({"a": 0.03, "b": 0.05})[1] == "PROCEED_TIMELINESS"
    assert verdict({"a": 0.09, "b": 0.20})[1] == "PROCEED_PERFORMANCE"
    assert verdict({})[1] == "NO_DATA"
    # Boundary: exactly 2% is not < 2% -> timeliness, not pivot.
    assert verdict({"a": 0.02})[1] == "PROCEED_TIMELINESS"
    # Controls are excluded from the median.
    import io
    csv_text = ("workload,config,hslot_frac\n"
                "lbm,B2,0.10\nleela,B2,0.001\nmcf,B1,0.99\n")
    rows = {}
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        if row["config"] == "B2" and row["workload"] not in {"leela"}:
            rows[row["workload"]] = float(row["hslot_frac"])
    assert rows == {"lbm": 0.10}, rows
    # A non-numeric hslot_frac cell is skipped, not fatal.
    import tempfile, os as _os
    fd, path = tempfile.mkstemp(suffix=".csv")
    with _os.fdopen(fd, "w") as fh:
        fh.write("# comment\nworkload,config,hslot_frac\n"
                 "good,B2,0.05\nbad,B2,N/A\n")
    parsed = load_b2_memintensive(path, set())
    _os.unlink(path)
    assert parsed == {"good": 0.05}, parsed
    print("gate_g1 selftest: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", nargs="?", help="results/phase1_hslot.csv")
    ap.add_argument("--controls", default="",
                    help="comma-separated R3 no-harm control workloads")
    ap.add_argument("--controls-file",
                    help="file with one control workload per line")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.csv:
        ap.error("csv is required unless --selftest")
    controls = set(c for c in args.controls.split(",") if c)
    if args.controls_file:
        with open(args.controls_file) as f:
            controls |= {ln.strip() for ln in f if ln.strip()}
    data = load_b2_memintensive(args.csv, controls)
    tag, text = render(data)
    print(text)
    return 0 if tag != "NO_DATA" else 2


if __name__ == "__main__":
    sys.exit(main())
