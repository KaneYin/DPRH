#!/usr/bin/env python3
"""DPRH Phase 1: assert a run_se.py run measured the intended instruction window.

Regression guard for the run_se.py warmup/measure fix (gem5 d2e6076): before that
fix, setting system.o3.max_insts_any_thread AFTER m5.instantiate() was a no-op, so
the "measure" phase ran zero (or all) instructions instead of --measure. That bug
is invisible to unit tests -- it only shows up in a real simulation -- so this
checker parses the post-reset instruction count from a run_se.py stats.txt and
asserts it matches --measure within a small tolerance (scheduleInstStop exits at
current + N committed insts, so the match is near-exact modulo the exit boundary).

The measured count is read from `simInsts` (global committed insts since the last
m5.stats.reset(), which run_se.py calls at the warmup->measure boundary). Fallback
stat names are tried for gem5-version robustness.

Usage:
    check_inst_window.py <outdir-or-stats.txt> --expect 100000000 [--tol-frac 0.005]
    check_inst_window.py --selftest

Exit code: 0 = within tolerance (PASS), 1 = out of tolerance (FAIL),
2 = the instruction stat was not found in stats.txt.
"""
import argparse
import os
import re
import sys

# simInsts is the global committed-instruction count since the last stats.reset()
# and is the most version-stable signal. The o3-scoped names are fallbacks.
DEFAULT_STATS = [
    "simInsts",
    "system.o3.commitStats0.numInsts",
    "system.o3.numInsts",
    "system.o3.commit.committedInsts",
]


def parse_stat(stats_path, stat_names):
    """Return the first matching stat value found in stats_path, or None."""
    if not os.path.isfile(stats_path):
        return None
    with open(stats_path) as f:
        text = f.read()
    for name in stat_names:
        m = re.search(
            r"^" + re.escape(name) + r"\s+([-\d.eE+]+)", text, re.MULTILINE
        )
        if m:
            return float(m.group(1)), name
    return None


def within_tolerance(measured, expected, tol_frac):
    """True iff |measured - expected| <= max(2, tol_frac * expected)."""
    allowed = max(2.0, tol_frac * expected)
    return abs(measured - expected) <= allowed


def resolve_stats_path(arg):
    """Accept either a stats.txt file or an outdir containing stats.txt."""
    if os.path.isdir(arg):
        return os.path.join(arg, "stats.txt")
    return arg


def selftest():
    # Exact hit and a 1-instruction boundary miss both pass (floor tolerance 2).
    assert within_tolerance(100_000_000, 100_000_000, 0.005)
    assert within_tolerance(99_999_999, 100_000_000, 0.005)
    # A 1% shortfall on a 100M window is far outside 0.5% -> fail.
    assert not within_tolerance(99_000_000, 100_000_000, 0.005)
    # The classic Fix-A failure signature: measure phase ran ~0 insts -> fail.
    assert not within_tolerance(0, 100_000_000, 0.005)
    # Tiny windows: the floor tolerance of 2 instructions applies.
    assert within_tolerance(300_001, 300_000, 0.0)
    assert not within_tolerance(300_010, 300_000, 0.0)
    # parse_stat picks the first present name and returns (value, name).
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as fh:
        fh.write("simInsts       300000    # insts since reset\n"
                 "system.o3.numInsts  300000\n")
    val, name = parse_stat(path, DEFAULT_STATS)
    os.unlink(path)
    assert (val, name) == (300000.0, "simInsts"), (val, name)
    print("check_inst_window selftest: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", nargs="?",
                    help="run_se.py outdir or its stats.txt")
    ap.add_argument("--expect", type=int,
                    help="intended --measure instruction count")
    ap.add_argument("--tol-frac", type=float, default=0.005,
                    help="relative tolerance (default 0.5%%; floor 2 insts)")
    ap.add_argument("--stat", default=None,
                    help="override the instruction stat name")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.path or args.expect is None:
        ap.error("path and --expect are required unless --selftest")

    stats_path = resolve_stats_path(args.path)
    names = [args.stat] if args.stat else DEFAULT_STATS
    found = parse_stat(stats_path, names)
    if found is None:
        print(f"FAIL(2): no instruction stat {names} in {stats_path}")
        return 2
    measured, name = found
    ok = within_tolerance(measured, args.expect, args.tol_frac)
    print(f"{name} = {int(measured)} ; expected {args.expect} "
          f"(tol {args.tol_frac:.3%}, floor 2) -> {'PASS' if ok else 'FAIL'}")
    if not ok:
        print("  Instruction window is wrong. If measured is ~0 or ~whole "
              "program, the warmup/measure scheduleInstStop wiring regressed "
              "(see run_se.py / gem5 d2e6076).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
