#!/usr/bin/env python3
"""DPRH held-out (R5) dispatch guard (FIX-4).

The R5 held-out traces (research_plan.md) must be excluded from ALL tuning.
mpki_profile.py registers them mechanically in results/HELD_OUT.md at profiling
time; this guard enforces that registration for any run driver (smoke_test.sh
today, Phase-1 tuning dispatchers later).

Usage:
    dispatch_guard.py --check <bench>... [--final-run]
        Exit 0 if none of <bench> is held-out. If any is held-out:
          - without --final-run: print offenders and exit 3 (refuse).
          - with    --final-run: allow, and append an audit line to
            results/final_run.log recording who ran which held-out traces when.
    dispatch_guard.py --selftest
        Run self-tests (no gem5, no real HELD_OUT.md) and exit.

A <bench> matches a held-out entry by exact name or by basename, so path-style
launch specs (as smoke_test.sh passes) are matched against benchmark names.
If results/HELD_OUT.md does not exist yet, nothing is registered as held-out and
the guard allows the run (with a note) -- profiling has not happened.
"""
import argparse
import datetime
import getpass
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_HELD_OUT = os.path.join(REPO_ROOT, "results", "HELD_OUT.md")
DEFAULT_LOG = os.path.join(REPO_ROOT, "results", "final_run.log")


def held_out_names(path):
    """Parse the '## Held-out traces' bullet list from HELD_OUT.md.

    Returns a set of benchmark names. Missing file -> empty set (nothing
    registered). The sentinel '- (none ...)' line is ignored.
    """
    names = set()
    if not os.path.isfile(path):
        return names
    with open(path) as f:
        for line in f:
            m = re.match(r"^-\s+(\S+)", line)
            if m and not m.group(1).startswith("("):
                names.add(m.group(1))
    return names


def matches_held_out(bench, held):
    return bench in held or os.path.basename(bench) in held


def run_check(benches, held, final_run, log_file, argv):
    """Return (exit_code, offenders). Logs on an allowed --final-run."""
    offenders = [b for b in benches if matches_held_out(b, held)]
    if not offenders:
        return 0, offenders
    if not final_run:
        return 3, offenders
    # Allowed final run over held-out traces: record an audit trail.
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    with open(log_file, "a") as f:
        f.write(
            f"{datetime.datetime.now().isoformat(timespec='seconds')}\t"
            f"user={getpass.getuser()}\theld_out={','.join(offenders)}\t"
            f"argv={' '.join(argv)}\n"
        )
    return 0, offenders


def _selftest():
    import tempfile
    d = tempfile.mkdtemp()
    held_md = os.path.join(d, "HELD_OUT.md")
    log = os.path.join(d, "final_run.log")
    with open(held_md, "w") as f:
        f.write("## Held-out traces (EXCLUDED FROM ALL TUNING)\n")
        f.write("- benchA  (rank 5, MPKI 3.1)\n")
        f.write("- benchB  (rank 10, MPKI 2.0)\n")
        f.write("- (none -- sentinel, must be ignored)\n")
    held = held_out_names(held_md)
    assert held == {"benchA", "benchB"}, held  # sentinel '(none' ignored

    # held-out, no --final-run -> refuse (3)
    code, off = run_check(["benchA"], held, False, log, ["x"])
    assert code == 3 and off == ["benchA"], (code, off)
    assert not os.path.exists(log)  # nothing logged on refusal

    # non-held-out -> allow (0)
    code, off = run_check(["benchC"], held, False, log, ["x"])
    assert code == 0 and off == [], (code, off)

    # held-out with --final-run -> allow (0) and log
    code, off = run_check(["/some/dir/benchB"], held, True, log, ["y", "z"])
    assert code == 0 and off == ["/some/dir/benchB"], (code, off)
    assert os.path.isfile(log)
    assert "held_out=/some/dir/benchB" in open(log).read()

    # missing HELD_OUT.md -> nothing registered -> allow
    assert held_out_names(os.path.join(d, "nope.md")) == set()

    print("[guard] selftest OK (refuse / allow / final-run audit)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", nargs="+", metavar="BENCH",
                    help="benchmark launch specs to validate")
    ap.add_argument("--final-run", action="store_true",
                    help="permit running held-out traces (logged as an audit "
                         "trail); required to touch R5")
    ap.add_argument("--held-out-file", default=DEFAULT_HELD_OUT)
    ap.add_argument("--log-file", default=DEFAULT_LOG)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    if not args.check:
        print("[guard] ERROR: pass --check <bench>... (or --selftest)",
              file=sys.stderr)
        return 2

    held = held_out_names(args.held_out_file)
    if not held:
        print(f"[guard] no held-out set registered ({args.held_out_file} "
              f"absent/empty); allowing.")
        return 0

    code, offenders = run_check(args.check, held, args.final_run, args.log_file,
                                sys.argv)
    if code == 3:
        print(f"[guard] REFUSED: {offenders} are R5 held-out traces (excluded "
              f"from all tuning). Re-run with --final-run only for the final, "
              f"pre-registered evaluation.", file=sys.stderr)
    elif offenders and args.final_run:
        print(f"[guard] FINAL RUN over held-out {offenders} -- logged to "
              f"{args.log_file}.")
    return code


if __name__ == "__main__":
    sys.exit(main())
