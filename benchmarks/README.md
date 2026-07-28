# SPEC CPU benchmarks — gem5 SE-mode cross-compile notes (Task 11)

SPEC CPU2006 + CPU2017 are licensed but not committed to this repo (binaries and
SPEC sources are git-ignored via `benchmarks/spec*/`). This file records how the
SE-mode-runnable subset is built and which benchmarks are excluded, and defines
the manifest that `scripts/mpki_profile.py` consumes.

> All build steps here are **[cluster]** (Linux, cross-compile toolchain). This
> file is authored on macOS; fill the empty tables on the cluster after building.

## A0 decision (see plan/refs/A0_findings.md)
- Suites: **SPEC CPU2006 AND CPU2017** (single-benchmark components).
- R2 selection criterion: **per-benchmark LLC MPKI >= 1** (DPRH's own criterion,
  not MSF's set-level > 10). Profiling is done under config **B1** with the
  **SPP** prefetcher.

## Cross-compile requirements (SE mode)
gem5 SE mode cannot fork/exec, JIT, or service arbitrary syscalls, so binaries
must be **statically linked** and free of runtime fork/exec/JIT.

Record on the cluster:
- SPEC version(s): __________ (e.g. CPU2006 v1.2, CPU2017 v1.1)
- Compiler + version: __________ (e.g. gcc 10.x, x86-64)
- Flags: __________ (must include static linking, e.g. `-static -O2`)
- libc: __________ (static glibc / musl)

## Exclusions (SE-mode-incompatible) — REQUIRED thesis artifact (R2)
List every benchmark excluded and why (Fortran runtime issues, fork/exec,
unsupported syscalls, dynamic-only, etc.). Fill on the cluster:

| Benchmark | Suite | Reason excluded |
| --- | --- | --- |
| (fill) | | |

## Manifest for scripts/mpki_profile.py
Create `benchmarks/benchmarks.tsv` (git-ignored with the binaries) with one
runnable benchmark per line, tab-separated:

```
# name<TAB>binary_path<TAB>args
bzip2_2006	benchmarks/spec2006/bin/bzip2	input.source 280
lbm_2017	benchmarks/spec2017/bin/lbm	2000 reference.dat 0 0 100_100_130_ldc.of
...
```

`scripts/mpki_profile.py --benchmarks-dir benchmarks/` reads this manifest,
runs each under B1/SPP for a fixed window, and emits `results/mpki_profile.csv`
classifying each benchmark as R2_main (MPKI>=1), R3_control (MPKI<0.5), or
middle.

## Included / runnable set (fill after cross-compile)
| Benchmark | Suite | Static? | Runs in SE? | Notes |
| --- | --- | --- | --- | --- |
| (fill) | | | | |
