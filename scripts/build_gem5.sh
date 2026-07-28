#!/usr/bin/env bash
set -euo pipefail
# Build gem5.opt (x86) on the Linux cluster and log the exact commit hash.
cd "$(dirname "$0")/../gem5"
COMMIT=$(git rev-parse HEAD)
echo "Building gem5 @ ${COMMIT}"
python3 $(command -v scons) build/X86/gem5.opt -j"$(nproc)"
echo "Built gem5.opt @ ${COMMIT} on $(hostname) at $(date -u)"
