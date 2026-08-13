#!/usr/bin/env python3
"""Audit and evaluate a manifest-driven DPRH Gate-G0 microbenchmark run."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sys


STAT_ALIASES = {
    "sim_insts": ["simInsts"],
    "ipc": [
        "system.o3.ipc",
        "system.o3.commitStats0.ipc",
        "system.cpu.ipc",
    ],
    "llc_demand_misses": [
        "system.llc.demandMisses::total",
        "system.llc.overallMisses::total",
    ],
    "read_row_hit_rate": [
        "system.mem_ctrl.dram.readRowHitRate",
        "system.mem_ctrl.dram.pageHitRate",
    ],
    "demand_read_latency_mean": [
        "system.mem_ctrl.demandReadLatency::mean",
    ],
    "pf_issued": [
        "system.cpu.l2cache.prefetcher.pfIssued",
    ],
    "prefetch_enqueued": [
        "system.mem_ctrl.prefetchEnqueued",
    ],
    "sched_cycles": [
        "system.mem_ctrl.schedCycles",
    ],
    "cycles_hslot": [
        "system.mem_ctrl.cyclesHslot",
    ],
}

REQUIRED_METRICS = {
    "sim_insts", "ipc", "llc_demand_misses", "read_row_hit_rate",
    "demand_read_latency_mean", "prefetch_enqueued", "sched_cycles",
    "cycles_hslot",
}

EXPECTED_PROFILE = {
    "B0": {
        "demand_first": "false",
        "enable_dprh": "false",
        "enable_filter": "false",
    },
    "B1": {
        "demand_first": "false",
        "enable_dprh": "false",
        "enable_filter": "true",
    },
    "B2": {
        "demand_first": "true",
        "enable_dprh": "false",
        "enable_filter": "true",
    },
}

FATAL_RE = re.compile(
    r"panic:|fatal:|Assertion|Traceback|Program aborted|BACKTRACE"
)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_stats(path):
    stats = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            fields = line.split()
            if len(fields) < 2:
                continue
            try:
                stats[fields[0]] = float(fields[1])
            except ValueError:
                continue
    return stats


def metric(stats, name, default=None):
    for alias in STAT_ALIASES[name]:
        if alias in stats:
            return stats[alias]
    return default


def parse_ini_section(path, section):
    values = {}
    current = None
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1]
                continue
            if current == section and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().lower()
    return values


def relative_delta(left, right):
    denominator = abs(left)
    if denominator == 0:
        return math.inf if right != 0 else 0.0
    return abs(right - left) / denominator


def evaluate_gates(rows, gates):
    by_cell = {(row["workload"], row["config"]): row for row in rows}
    workloads = sorted({row["workload"] for row in rows})

    speedups = {}
    speedup_passes = []
    scheduler_deltas = {}
    scheduler_passes = []
    for workload in workloads:
        b0 = by_cell[(workload, "B0")]
        b1 = by_cell[(workload, "B1")]
        b2 = by_cell[(workload, "B2")]
        speedup = b1["ipc"] / b0["ipc"] - 1.0
        speedups[workload] = speedup
        if speedup >= gates["min_speedup_fraction"]:
            speedup_passes.append(workload)

        deltas = {
            "ipc_fraction": relative_delta(b1["ipc"], b2["ipc"]),
            "row_hit_points": abs(
                b2["read_row_hit_rate"] - b1["read_row_hit_rate"]
            ),
            "demand_latency_fraction": relative_delta(
                b1["demand_read_latency_mean"],
                b2["demand_read_latency_mean"],
            ),
        }
        scheduler_deltas[workload] = deltas
        if (
            deltas["ipc_fraction"] >= gates["min_ipc_delta_fraction"]
            or deltas["row_hit_points"] >= gates["min_row_hit_delta_points"]
            or deltas["demand_latency_fraction"]
            >= gates["min_demand_latency_delta_fraction"]
        ):
            scheduler_passes.append(workload)

    g0a = len(speedup_passes) >= gates["min_speedup_workloads"]
    g0b = len(scheduler_passes) >= gates["min_scheduler_delta_workloads"]
    b1_rows = [row for row in rows if row["config"] == "B1"]
    ipc_values = [row["ipc"] for row in b1_rows]
    row_hit_values = [row["read_row_hit_rate"] for row in b1_rows]
    ipc_span = (max(ipc_values) - min(ipc_values)) / min(ipc_values)
    row_hit_span = max(row_hit_values) - min(row_hit_values)
    kernel_behavior_distinct = (
        ipc_span >= gates["min_kernel_ipc_span_fraction"]
        and row_hit_span >= gates["min_kernel_row_hit_span_points"]
    )
    return {
        "g0a_prefetch_speedup": g0a,
        "g0b_scheduler_distinguishable": g0b,
        "kernel_behavior_distinct": kernel_behavior_distinct,
        "kernel_ipc_span_fraction": ipc_span,
        "kernel_row_hit_span_points": row_hit_span,
        "speedups": speedups,
        "speedup_pass_workloads": speedup_passes,
        "scheduler_deltas": scheduler_deltas,
        "scheduler_delta_workloads": scheduler_passes,
    }


def audit_cell(cell, manifest, expected_measure):
    outdir = Path(cell["outdir"])
    log_path = Path(cell["log"])
    stats_path = outdir / "stats.txt"
    config_path = outdir / "config.ini"
    errors = []

    if cell.get("returncode") != 0:
        errors.append(f"simulator return code is {cell.get('returncode')}")
    for path, label in (
        (log_path, "log"), (stats_path, "stats.txt"),
        (config_path, "config.ini"),
    ):
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"missing or empty {label}: {path}")
    if errors:
        return None, errors

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    if FATAL_RE.search(log_text):
        errors.append("fatal-error signature found in log")
    for marker in (
        "[dprh] ff exit:",
        "[dprh] warmup exit: dprh warmup complete",
        "[dprh] measure exit: dprh measure complete",
        f"[dprh] done config={cell['config']}",
        f"[dprh-micro] kernel begin mode={cell['mode']}",
    ):
        if marker not in log_text:
            errors.append(f"missing progress marker: {marker}")

    profile = parse_ini_section(config_path, "system.mem_ctrl")
    for key, expected in EXPECTED_PROFILE[cell["config"]].items():
        if profile.get(key) != expected:
            errors.append(
                f"profile {key}={profile.get(key)!r}, expected {expected!r}"
            )

    raw_stats = parse_stats(stats_path)
    values = {
        name: metric(raw_stats, name) for name in STAT_ALIASES
    }
    for name in REQUIRED_METRICS:
        if values[name] is None or not math.isfinite(values[name]):
            errors.append(f"missing/non-finite stat: {name}")

    if errors:
        return None, errors

    allowed = max(2.0, 0.005 * expected_measure)
    if abs(values["sim_insts"] - expected_measure) > allowed:
        errors.append(
            f"simInsts={values['sim_insts']:g}, expected {expected_measure}"
        )
    if values["ipc"] <= 0:
        errors.append("IPC must be positive")
    if values["llc_demand_misses"] <= 0:
        errors.append("LLC demand misses must be positive for a G0 kernel")
    if not 0 <= values["read_row_hit_rate"] <= 100:
        errors.append("read row-hit rate must lie in [0, 100]")
    if values["demand_read_latency_mean"] <= 0:
        errors.append("mean demand-read latency must be positive")
    if values["sched_cycles"] <= 0:
        errors.append("schedCycles must be positive")
    if not 0 <= values["cycles_hslot"] <= values["sched_cycles"]:
        errors.append("cyclesHslot must lie in [0, schedCycles]")

    pf_issued = values["pf_issued"] or 0.0
    if cell["config"] == "B0":
        if pf_issued != 0 or values["prefetch_enqueued"] != 0:
            errors.append("B0 must issue/enqueue zero hardware prefetches")
    elif cell["expect_prefetch"]:
        if pf_issued <= 0 or values["prefetch_enqueued"] <= 0:
            errors.append("B1/B2 expected positive prefetch issue/enqueue counts")

    row = {
        "workload": cell["workload"],
        "config": cell["config"],
        **values,
        "pf_issued": pf_issued,
        "outdir": str(outdir),
        "log": str(log_path),
    }
    return row, errors


def analyze(run_manifest_path, emit=True):
    with open(run_manifest_path, encoding="utf-8") as handle:
        run = json.load(handle)
    source_path = Path(run["micro_source"])
    binary_path = Path(run["binary"])
    if not source_path.is_file() or not binary_path.is_file():
        raise ValueError("recorded microbenchmark source or binary is missing")
    if sha256_file(source_path) != run["micro_source_sha256"]:
        raise ValueError("microbenchmark source changed after dispatch")
    if sha256_file(binary_path) != run["binary_sha256"]:
        raise ValueError("microbenchmark binary changed after dispatch")
    manifest_path = Path(run["manifest"])
    if not manifest_path.is_file():
        raise ValueError(f"experiment manifest missing: {manifest_path}")
    if sha256_file(manifest_path) != run["manifest_sha256"]:
        raise ValueError("experiment manifest changed after dispatch")
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)

    if run["suite"] != "g0":
        raise ValueError("Gate-G0 analyzer requires a run with suite=g0")
    if run.get("windows") != manifest.get("windows"):
        raise ValueError("run windows differ from the frozen manifest")
    if run.get("configs") != manifest.get("configs"):
        raise ValueError("run configs differ from the frozen manifest")
    expected_names = [
        item["name"] for item in manifest["workloads"]
        if "g0" in item["suites"]
    ]
    workload_by_name = {
        item["name"]: item for item in manifest["workloads"]
    }
    expected_cells = {
        (name, config) for name in expected_names
        for config in manifest["configs"]
    }
    actual_cells = {
        (cell["workload"], cell["config"]) for cell in run["cells"]
    }
    if actual_cells != expected_cells or len(run["cells"]) != len(expected_cells):
        raise ValueError("run manifest does not contain the exact G0 matrix")
    for cell in run["cells"]:
        workload = workload_by_name[cell["workload"]]
        expected_options = " ".join(str(value) for value in (
            workload["mode"], workload["stream_mib"], workload["random_mib"],
            workload["pattern_arg"], workload["batches"], workload["seed"],
        ))
        if (
            cell.get("mode") != workload["mode"]
            or cell.get("role") != workload["role"]
            or cell.get("expect_prefetch") != workload["expect_prefetch"]
            or cell.get("options") != expected_options
        ):
            raise ValueError(
                f"cell metadata differs from manifest: {cell['workload']}"
            )

    rows = []
    audit_errors = []
    for cell in run["cells"]:
        row, errors = audit_cell(
            cell, manifest, manifest["windows"]["measure"]
        )
        if errors:
            audit_errors.extend(
                f"{cell['workload']}/{cell['config']}: {error}"
                for error in errors
            )
        elif row is not None:
            rows.append(row)

    result_root = Path(run_manifest_path).resolve().parent
    summary_csv = result_root / "g0_summary.csv"
    verdict_path = result_root / "g0_verdict.json"
    gate_result = None
    if not audit_errors:
        gate_result = evaluate_gates(rows, manifest["gates"])

    fieldnames = [
        "workload", "config", "sim_insts", "ipc", "llc_demand_misses",
        "read_row_hit_rate", "demand_read_latency_mean", "pf_issued",
        "prefetch_enqueued", "sched_cycles", "cycles_hslot", "outdir", "log",
    ]
    with open(summary_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: (
            expected_names.index(row["workload"]),
            manifest["configs"].index(row["config"]),
        )))

    passed = bool(
        not audit_errors
        and gate_result
        and gate_result["g0a_prefetch_speedup"]
        and gate_result["g0b_scheduler_distinguishable"]
        and gate_result["kernel_behavior_distinct"]
    )
    verdict = {
        "matrix_pass": passed,
        "audit_errors": audit_errors,
        "thresholds": manifest["gates"],
        "gate_result": gate_result,
        "scope": (
            "This evaluates G0.a/G0.b only. Full G0 also requires the "
            "previously established V1 and isolated calibration evidence."
        ),
    }
    with open(verdict_path, "w", encoding="utf-8") as handle:
        json.dump(verdict, handle, indent=2, sort_keys=True)
        handle.write("\n")

    if emit:
        print(f"G0 summary: {summary_csv}")
        for row in sorted(rows, key=lambda item: (
            expected_names.index(item["workload"]),
            manifest["configs"].index(item["config"]),
        )):
            print(
                f"{row['workload']:16s} {row['config']} "
                f"IPC={row['ipc']:.6f} rowhit={row['read_row_hit_rate']:.2f}% "
                f"demandLat={row['demand_read_latency_mean']:.2f} "
                f"pfEnq={row['prefetch_enqueued']:.0f}"
            )
        for error in audit_errors:
            print(f"FAIL audit: {error}")
        if gate_result:
            for workload, speedup in gate_result["speedups"].items():
                deltas = gate_result["scheduler_deltas"][workload]
                print(
                    f"{workload}: B1/B0 IPC delta={speedup:+.3%}; "
                    f"B2-vs-B1 IPC={deltas['ipc_fraction']:.3%}, "
                    f"rowhit={deltas['row_hit_points']:.3f} points, "
                    f"demandLat={deltas['demand_latency_fraction']:.3%}"
                )
            print(
                "G0.a prefetch speedup: "
                + ("PASS" if gate_result["g0a_prefetch_speedup"] else "FAIL")
            )
            print(
                "G0.b scheduler distinguishability: "
                + ("PASS" if gate_result["g0b_scheduler_distinguishable"] else "FAIL")
            )
            print(
                "Kernel behavior distinguishability: "
                + ("PASS" if gate_result["kernel_behavior_distinct"] else "FAIL")
                + f" (B1 IPC span={gate_result['kernel_ipc_span_fraction']:.3%}, "
                + f"row-hit span={gate_result['kernel_row_hit_span_points']:.3f} points)"
            )
        print(
            "PASS: G0 workload matrix (G0.a/G0.b)"
            if passed else "FAIL: G0 workload matrix remains open"
        )
        print(verdict["scope"])
        print(f"G0 verdict: {verdict_path}")
    return 0 if passed else 1


def selftest():
    gates = {
        "min_speedup_fraction": 0.01,
        "min_speedup_workloads": 2,
        "min_scheduler_delta_workloads": 1,
        "min_ipc_delta_fraction": 0.005,
        "min_row_hit_delta_points": 0.5,
        "min_demand_latency_delta_fraction": 0.005,
        "min_kernel_ipc_span_fraction": 0.01,
        "min_kernel_row_hit_span_points": 1.0,
    }
    rows = []
    for index, workload in enumerate((
        "stream_main", "stride_main", "mix_main"
    )):
        rows.extend([
            {"workload": workload, "config": "B0", "ipc": 1.0,
             "read_row_hit_rate": 20.0, "demand_read_latency_mean": 100.0},
            {"workload": workload, "config": "B1", "ipc": 1.1 + 0.02 * index,
             "read_row_hit_rate": 30.0 + 5.0 * index,
             "demand_read_latency_mean": 90.0},
            {"workload": workload, "config": "B2", "ipc": 1.08 + 0.02 * index,
             "read_row_hit_rate": 32.0 + 5.0 * index,
             "demand_read_latency_mean": 92.0},
        ])
    verdict = evaluate_gates(rows, gates)
    assert verdict["g0a_prefetch_speedup"]
    assert verdict["g0b_scheduler_distinguishable"]
    assert verdict["kernel_behavior_distinct"]

    for row in rows:
        if row["config"] == "B1":
            row["ipc"] = 1.001
            row["read_row_hit_rate"] = 30.0
            row["demand_read_latency_mean"] = 90.0
        if row["config"] == "B2":
            row["ipc"] = 1.001
            row["read_row_hit_rate"] = 30.0
            row["demand_read_latency_mean"] = 90.0
    verdict = evaluate_gates(rows, gates)
    assert not verdict["g0a_prefetch_speedup"]
    assert not verdict["g0b_scheduler_distinguishable"]
    assert not verdict["kernel_behavior_distinct"]
    print("analyze_micro_g0 selftest: OK")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_manifest", nargs="?", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.run_manifest is None:
        parser.error("run_manifest is required unless --selftest is used")
    try:
        return analyze(args.run_manifest.resolve())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
