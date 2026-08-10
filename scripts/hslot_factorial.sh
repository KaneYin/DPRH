#!/usr/bin/env bash
# DPRH Phase 1 (R6): synthetic 3x3 H_slot attribution factorial.
# Sweeps demand injection intensity (period) x prefetch row-locality density
# (num_seq_pkts), for configs B1 and B2, tagging the prefetch stream. Emits one
# gem5 outdir per (config, period, seq) cell under results/runs/factorial/.
#
# Usage: scripts/hslot_factorial.sh <path-to-gem5.opt> [A_GUARD_CYCLES]
set -euo pipefail

GEM5="${1:?usage: hslot_factorial.sh <gem5.opt> [a_guard]}"
A_GUARD="${2:-64}"
CFG_SCRIPT="gem5/configs/dprh/run_trafficgen.py"
OUT_ROOT="results/runs/factorial"
mkdir -p "$OUT_ROOT"

# R6: >=3 levels each, calibrated low/med/high. Small periods = high demand
# pressure; large num_seq_pkts = high prefetch row locality (~10/50/90%).
PERIODS=(250 1000 4000)      # high / med / low demand intensity (ticks)
SEQ=(1 4 16)                 # low / med / high prefetch row-locality density
CONFIGS=(B1 B2)
# Keep the prefetch injection rate fixed while demand pressure changes. Before
# the calibration fix, --demand-period silently controlled both generators.
PF_PERIOD=1000

for cfg in "${CONFIGS[@]}"; do
  for per in "${PERIODS[@]}"; do
    for seq in "${SEQ[@]}"; do
      out="${OUT_ROOT}/${cfg}_p${per}_s${seq}"
      echo "[factorial] ${cfg} period=${per} seq=${seq} -> ${out}"
      "$GEM5" --outdir="$out" "$CFG_SCRIPT" \
        --config "$cfg" --demand-period "$per" --pf-period "$PF_PERIOD" \
        --pf-seq-pkts "$seq" \
        --pf-tag --a-guard "$A_GUARD"
    done
  done
done
echo "[factorial] done -> ${OUT_ROOT}"
