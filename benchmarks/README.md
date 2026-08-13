# DPRH C microbenchmarks

The project uses controlled C microbenchmarks rather than SPEC or another
real-application suite.  This intentionally supports a design-space claim—when
the harvestable prefetch-row-hit opportunity appears—not a claim about its
prevalence in production workloads.

## Source and interface

`micro/dprh_memmix.c` is the version-controlled source used by the full O3
`run_se.py` experiments.  It has one fixed command-line shape:

```text
dprh_memmix MODE STREAM_MIB RANDOM_MIB PATTERN_ARG BATCHES SEED
```

| Mode | Memory behavior | `PATTERN_ARG` |
|---|---|---|
| `stream` | unit-stride cache-line reads | sequential reads per batch |
| `stride` | fixed cache-line stride | stride in cache lines |
| `gather` | independent reads through a shuffled index array | reserved |
| `chase` | serialized pointer cycle through shuffled cache lines | dependent steps per batch |
| `mix` | streaming reads interleaved with one dependent chase | stream reads per chase |
| `compute` | integer recurrence with no allocated working set | operations per batch |
| `mixed` | legacy stream-plus-gather kernel used by C18 | sequential reads per batch |

`mixed` is retained only to reproduce the pre-G0 C18 accounting regression.
New experiments use `mix`, whose random component is dependency-serialized as
required by the research plan.

The program initializes deterministic data from `SEED`, prints the effective
parameters before entering the steady-state kernel, and accumulates all loaded
values into a volatile sink.  `BATCHES` is deliberately much longer than the
simulated region; `run_se.py` stops each phase by committed instruction count.

## Frozen experiment manifest

`micro/manifest.json` is the pre-registration artifact.  It freezes:

- the identical 200M fast-forward, 1M warmup, and 5M measurement schedule;
- the three Gate-G0 kernels (`stream_main`, `stride_main`, `mix_main`);
- a 64 MiB serialized chase stress point;
- two no-harm controls (`compute_control`, `chase_llc_control`);
- two held-out parameter points, which the runner refuses without
  `--final-run`;
- the G0.a/G0.b thresholds, before any matrix result is observed.

The three G0 working sets exceed the frozen 2 MiB LLC.  The 1 MiB chase control
is intentionally LLC-resident after warmup.

## Cluster build

Build on a Slurm compute node with the already installed musl toolchain.  Do
not commit the binary; `m5out/` is ignored.

```bash
cd /mmfs1/scratch/yqu30/DPRH

MICRO_CC="/mmfs1/scratch/yqu30/tools/musl-1.2.6/bin/musl-gcc"
MICRO_SOURCE="benchmarks/micro/dprh_memmix.c"
MICRO_BIN="m5out/bin/dprh_memmix"

mkdir -p m5out/bin results

"${MICRO_CC}" \
  -O3 \
  -std=c11 \
  -static \
  -march=x86-64 \
  -mtune=generic \
  -fno-tree-vectorize \
  -fno-stack-protector \
  -Wall \
  -Wextra \
  -Werror \
  "${MICRO_SOURCE}" \
  -o "${MICRO_BIN}"
```

Verify the artifact before simulation:

```bash
test -x "${MICRO_BIN}"
file "${MICRO_BIN}"
readelf -h "${MICRO_BIN}" | grep -E 'Class:|Machine:'

if readelf -l "${MICRO_BIN}" | grep -q INTERP
then
  echo "FAIL: dynamically linked binary"
else
  echo "PASS: static x86-64 binary"
fi
```

Short native checks exercise the newly added mode dispatch without attempting
the full `BATCHES` value:

```bash
"${MICRO_BIN}" stream 1 0 4 1000 1
"${MICRO_BIN}" stride 1 0 16 1000 1
"${MICRO_BIN}" chase 0 1 16 1000 1
"${MICRO_BIN}" mix 1 1 4 1000 1
"${MICRO_BIN}" compute 0 0 32 1000 1
```

## Gate-G0 dispatch

The compatibility wrapper now invokes the manifest-driven runner:

```bash
bash scripts/smoke_test.sh
```

The runner does not build anything.  It records Git and binary provenance,
runs the exact 3 × 3 `{kernel} × {B0,B1,B2}` matrix, and invokes
`scripts/analyze_micro_g0.py`.  Raw outputs live under ignored directories
`m5out/<run-tag>/` and `results/runs/<run-tag>/`; accepted results are copied
into `results/PHASE_LOG.md` only after cluster review.
