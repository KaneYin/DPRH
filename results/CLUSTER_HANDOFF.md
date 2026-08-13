# DPRH Cluster Handoff — Current Procedure

This is the only active Cluster runbook. It supersedes the retired SPEC/MPKI/R2
handoff. All measured results are microbenchmark-only and characterize a
controlled design space, not real-workload prevalence.

## Current state and next gate

- Outer repository: `main`; pull from `origin`.
- Inner gem5: outer repository pins `research/dprh` commit `72137d00b0`.
- C18: gem5 build, 13 H_slot tests, and repaired mixed-B1 accounting passed.
- G0.c: V1 passed (`pfIssued=499`, `prefetchEnqueued=314`).
- G0.d: isolated prefetch-locality calibration passed
  (`0.02% < 74.84% < 93.30%`).
- **Next:** compile the updated C binary and run the pre-registered 9-cell
  `{stream_main,stride_main,mix_main} × {B0,B1,B2}` matrix for G0.a/G0.b.

Do not run Phase 1, controls, or held-out points until this matrix is reviewed.

## C19.1 — Sync on the login node

```bash
cd /mmfs1/scratch/yqu30/DPRH

git status --short
git -C gem5 status --short

git pull --ff-only origin main
git submodule sync --recursive
git submodule update --init --recursive

git rev-parse --short HEAD
git -C gem5 rev-parse --short HEAD
git submodule status
```

The inner SHA must be `72137d00b0`. The outer SHA is the commit containing the
microbenchmark manifest/runner. Stop if either tracked worktree is dirty after
the update; ignored `m5out/`, build files, and logs are expected.

## C19.2 — Enter a compute allocation

Use the account/partition required by the Lakeshore cluster guide. A minimal
interactive shape is one task and 8 CPUs; request enough time for nine short
O3 simulations:

```bash
salloc --nodes=1 --ntasks=1 --cpus-per-task=8 --time=02:00:00
srun --pty bash -l
```

After the prompt moves to a compute host:

```bash
cd /mmfs1/scratch/yqu30/DPRH
source .venv-hpc/bin/activate

hostname
echo "SLURM_JOB_ID=${SLURM_JOB_ID:-missing}"
```

If the site requires explicit `--account` or `--partition`, add the values from
the cluster guide to `salloc`; do not run the measured matrix on a login node.

## C19.3 — Script and provenance checks

These checks do not build or run gem5:

```bash
bash -n scripts/smoke_test.sh
python3 scripts/run_micro_g0.py --selftest
python3 scripts/analyze_micro_g0.py --selftest
python3 -m json.tool benchmarks/micro/manifest.json >/dev/null
```

Expected: both Python scripts print `selftest: OK` and all return codes are 0.

Verify the existing simulator; no gem5 rebuild is required because C19 changes
only the outer C benchmark and experiment tooling:

```bash
test -x gem5/build/X86/gem5.opt
./gem5/build/X86/gem5.opt --build-info | head -5
git -C gem5 rev-parse --short HEAD
```

## C19.4 — Compile the updated static C binary

```bash
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

COMPILE_RC=$?
echo "micro compile exit code: ${COMPILE_RC}"
```

Then verify the actual artifact:

```bash
test "${COMPILE_RC}" -eq 0
test -x "${MICRO_BIN}"
file "${MICRO_BIN}"
readelf -h "${MICRO_BIN}" | grep -E 'Class:|Machine:'

if readelf -l "${MICRO_BIN}" | grep -q INTERP
then
  echo "FAIL: dynamically linked binary"
else
  echo "PASS: static x86-64 binary"
fi

sha256sum "${MICRO_SOURCE}" "${MICRO_BIN}"
```

Expected: compile exit 0, ELF64 x86-64, statically linked, and no `INTERP`.

## C19.5 — Native mode-dispatch smoke

These short runs verify every active kernel path before spending simulation
time:

```bash
"${MICRO_BIN}" stream 1 0 4 1000 1
"${MICRO_BIN}" stride 1 0 16 1000 1
"${MICRO_BIN}" chase 0 1 16 1000 1
"${MICRO_BIN}" mix 1 1 4 1000 1
"${MICRO_BIN}" compute 0 0 32 1000 1
```

Every command must print `kernel begin`, `complete`, and return 0. In
particular, `mix` is stream plus dependency-serialized chase; legacy `mixed`
remains only for reproducing C18.

## C19.6 — Preview and run the exact G0 matrix

Previewing proves which nine commands will run without launching gem5:

```bash
python3 scripts/run_micro_g0.py --dry-run
```

Confirm the preview contains only:

```text
stream_main: B0 B1 B2
stride_main: B0 B1 B2
mix_main:    B0 B1 B2
```

Then dispatch from the compute node:

```bash
bash scripts/smoke_test.sh
G0_RC=$?
echo "G0 matrix exit code: ${G0_RC}"
```

The runner creates a unique UTC-tagged directory, records outer/gem5 Git SHA,
source/binary/manifest SHA256, host, Slurm job, every command and return code,
and invokes the analyzer automatically. Individual logs are saved under
`results/runs/<run-tag>/`; no extra `tee` wrapper is required.

For every cell the analyzer requires:

- simulator return code 0 and no panic/fatal/assertion/abort/backtrace;
- the expected kernel mode and complete Atomic/O3 warmup/O3 measure markers;
- the exact B0/B1/B2 MemCtrl profile;
- approximately 5,000,000 post-reset instructions and positive IPC;
- valid demand traffic, row-hit rate, demand latency, and H_slot bounds;
- zero prefetch issue/enqueue for B0 and positive values for B1/B2.

The final lines report:

```text
G0.a prefetch speedup: PASS|FAIL
G0.b scheduler distinguishability: PASS|FAIL
Kernel behavior distinguishability: PASS|FAIL
PASS: G0 workload matrix (G0.a/G0.b)
```

If the matrix fails, preserve the run directory and report the complete
analyzer summary. Do not edit workload parameters or thresholds after seeing a
failure; the next task is root-cause analysis.

## C19.7 — Locate and re-audit the result

```bash
LATEST_G0=$(ls -1dt results/runs/micro_g0_* | head -1)
echo "LATEST_G0=${LATEST_G0}"

python3 scripts/analyze_micro_g0.py "${LATEST_G0}/run_manifest.json"

sed -n '1,40p' "${LATEST_G0}/g0_summary.csv"
sed -n '1,240p' "${LATEST_G0}/g0_verdict.json"
```

Send the analyzer output plus `LATEST_G0`. If G0.a/G0.b pass, combine them with
the already passed G0.c/G0.d evidence and sign full Gate G0 in
`results/PHASE_LOG.md`. Only then may Phase 1 characterization begin.

## Locked later suites

The following are intentionally **not** part of C19:

```bash
# Two W3 no-harm controls; run only after G0 review.
python3 scripts/run_micro_g0.py --suite controls

# Full non-held-out W2/W3 suite; run only when its phase is authorized.
python3 scripts/run_micro_g0.py --suite full

# W5 final-only points; guard refuses this command without --final-run.
python3 scripts/run_micro_g0.py --suite held_out --final-run
```

Never use `--final-run` during tuning.
