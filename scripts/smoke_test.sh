#!/usr/bin/env bash
set -euo pipefail

# Cluster-only compatibility entry point for the microbenchmark Gate-G0 matrix.
# The former positional-binary/SPEC workflow was retired by the 2026-08
# microbenchmark-only research-plan revision.  The Python driver owns workload
# selection, held-out protection, frozen windows, provenance, and analysis.

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

exec python3 "${REPO_ROOT}/scripts/run_micro_g0.py" "$@"
