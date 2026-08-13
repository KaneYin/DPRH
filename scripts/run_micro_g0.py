#!/usr/bin/env python3
"""Run the pre-registered DPRH C-microbenchmark matrix on a cluster node.

The default suite is the three-kernel Gate-G0 matrix.  This script never builds
gem5 or the benchmark binary; it records their hashes and runs the existing
artifacts with one frozen instruction schedule across B0, B1, and B2.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "benchmarks" / "micro" / "manifest.json"
GEM5 = REPO_ROOT / "gem5" / "build" / "X86" / "gem5.opt"
RUN_SE = REPO_ROOT / "gem5" / "configs" / "dprh" / "run_se.py"
ANALYZER = REPO_ROOT / "scripts" / "analyze_micro_g0.py"
MICRO_SOURCE = REPO_ROOT / "benchmarks" / "micro" / "dprh_memmix.c"
VALID_CONFIGS = ["B0", "B1", "B2"]
VALID_MODES = {
    "stream", "stride", "gather", "chase", "mix", "mixed", "compute"
}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_revision(path):
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def tracked_tree_dirty(path):
    for args in (["diff", "--quiet"], ["diff", "--cached", "--quiet"]):
        result = subprocess.run(["git", "-C", str(path), *args])
        if result.returncode not in (0, 1):
            raise RuntimeError(f"git {' '.join(args)} failed in {path}")
        if result.returncode == 1:
            return True
    return False


def load_manifest(path):
    with open(path, encoding="utf-8") as handle:
        manifest = json.load(handle)

    if manifest.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    if manifest.get("configs") != VALID_CONFIGS:
        raise ValueError("manifest configs must be exactly B0, B1, B2")

    windows = manifest.get("windows", {})
    for key in ("ff_offset", "warmup", "measure"):
        if not isinstance(windows.get(key), int) or windows[key] <= 0:
            raise ValueError(f"manifest windows.{key} must be a positive int")

    required_gates = {
        "min_speedup_fraction", "min_speedup_workloads",
        "min_scheduler_delta_workloads", "min_ipc_delta_fraction",
        "min_row_hit_delta_points", "min_demand_latency_delta_fraction",
        "min_kernel_ipc_span_fraction", "min_kernel_row_hit_span_points",
    }
    gates = manifest.get("gates", {})
    if not required_gates.issubset(gates):
        raise ValueError("manifest is missing one or more G0 thresholds")
    for key in required_gates:
        if not isinstance(gates[key], (int, float)) or gates[key] <= 0:
            raise ValueError(f"manifest gates.{key} must be positive")

    workloads = manifest.get("workloads", [])
    names = [item.get("name") for item in workloads]
    if not workloads or len(names) != len(set(names)) or None in names:
        raise ValueError("manifest workload names must be present and unique")

    required_keys = {
        "mode", "stream_mib", "random_mib", "pattern_arg", "batches",
        "seed", "role", "suites", "held_out", "expect_prefetch", "purpose"
    }
    for item in workloads:
        missing = sorted(required_keys - set(item))
        if missing:
            raise ValueError(f"{item['name']}: missing keys {missing}")
        if item["mode"] not in VALID_MODES:
            raise ValueError(f"{item['name']}: unsupported mode")
        for key in ("stream_mib", "random_mib", "pattern_arg", "batches", "seed"):
            if not isinstance(item[key], int) or item[key] < 0:
                raise ValueError(f"{item['name']}: {key} must be a nonnegative int")
        if not 1 <= item["pattern_arg"] <= 64:
            raise ValueError(f"{item['name']}: pattern_arg must be in [1, 64]")
        if item["stream_mib"] > 512 or item["random_mib"] > 512:
            raise ValueError(f"{item['name']}: working set exceeds C-kernel limit")
        if item["batches"] == 0 or item["seed"] == 0:
            raise ValueError(f"{item['name']}: batches/seed must be nonzero")
        mode = item["mode"]
        if mode in {"stream", "stride"} and not (
            item["stream_mib"] > 0 and item["random_mib"] == 0
        ):
            raise ValueError(f"{item['name']}: invalid stream/stride working sets")
        if mode in {"gather", "chase"} and not (
            item["stream_mib"] == 0 and item["random_mib"] > 0
        ):
            raise ValueError(f"{item['name']}: invalid gather/chase working sets")
        if mode in {"mix", "mixed"} and not (
            item["stream_mib"] > 0 and item["random_mib"] > 0
        ):
            raise ValueError(f"{item['name']}: mix modes require both working sets")
        if mode == "compute" and not (
            item["stream_mib"] == 0 and item["random_mib"] == 0
        ):
            raise ValueError(f"{item['name']}: compute working sets must be zero")

    g0 = [item for item in workloads if "g0" in item["suites"]]
    controls = [item for item in workloads if item["role"] == "no_harm_control"]
    held_out = [item for item in workloads if item["held_out"]]
    modes = {item["mode"] for item in workloads}
    if len(g0) != 3 or any(item["held_out"] for item in g0):
        raise ValueError("the g0 suite must contain exactly three non-held-out kernels")
    if len(controls) < 2:
        raise ValueError("manifest must pre-register at least two no-harm controls")
    if len(held_out) < 2:
        raise ValueError("manifest must reserve at least two held-out points")
    if not {"stream", "stride", "chase", "mix"}.issubset(modes):
        raise ValueError("manifest must cover stream, stride, chase, and mix")
    return manifest


def select_workloads(manifest, suite):
    return [item for item in manifest["workloads"] if suite in item["suites"]]


def workload_options(workload):
    values = [
        workload["mode"], workload["stream_mib"], workload["random_mib"],
        workload["pattern_arg"], workload["batches"], workload["seed"]
    ]
    return " ".join(str(value) for value in values)


def build_command(gem5, run_se, binary, outdir, config, workload, windows):
    return [
        str(gem5), f"--outdir={outdir}", str(run_se),
        "--config", config,
        "--prefetcher", "spp",
        "--cmd", str(binary),
        "--options", workload_options(workload),
        "--ff-offset", str(windows["ff_offset"]),
        "--warmup", str(windows["warmup"]),
        "--measure", str(windows["measure"]),
    ]


def save_metadata(path, metadata):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def run_logged(command, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log_handle.write(line)
            log_handle.flush()
        return process.wait()


def selftest():
    manifest = load_manifest(DEFAULT_MANIFEST)
    selected = select_workloads(manifest, "g0")
    assert [item["name"] for item in selected] == [
        "stream_main", "stride_main", "mix_main"
    ]
    assert not any(item["held_out"] for item in selected)
    command = build_command(
        Path("gem5.opt"), Path("run_se.py"), Path("dprh_memmix"),
        Path("out"), "B1", selected[0], manifest["windows"]
    )
    assert command[0] == "gem5.opt"
    assert "stream 64 0 4 2000000000 1" in command
    assert command[-2:] == ["--measure", "5000000"]
    print("run_micro_g0 selftest: OK")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--binary", type=Path, default=None)
    parser.add_argument(
        "--suite", choices=["g0", "controls", "full", "held_out"],
        default="g0",
    )
    parser.add_argument("--run-tag", default=None)
    parser.add_argument(
        "--final-run", action="store_true",
        help="required to dispatch any held-out workload",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    workloads = select_workloads(manifest, args.suite)
    if not workloads:
        parser.error(f"suite {args.suite!r} selects no workloads")
    if any(item["held_out"] for item in workloads) and not args.final_run:
        parser.error("held-out workloads require --final-run")

    binary = (args.binary or (REPO_ROOT / manifest["binary"])).resolve()
    if not args.dry_run:
        for path, label in ((GEM5, "gem5.opt"), (RUN_SE, "run_se.py"),
                            (MICRO_SOURCE, "microbenchmark source"),
                            (binary, "microbenchmark binary")):
            if not path.is_file():
                parser.error(f"{label} not found: {path}")
        if not os.access(GEM5, os.X_OK) or not os.access(binary, os.X_OK):
            parser.error("gem5.opt and the microbenchmark binary must be executable")
        if binary.stat().st_mtime < MICRO_SOURCE.stat().st_mtime:
            parser.error("microbenchmark binary is older than its source; rebuild it")
        if tracked_tree_dirty(REPO_ROOT) or tracked_tree_dirty(REPO_ROOT / "gem5"):
            parser.error("tracked source is dirty; commit or restore it before dispatch")

    gem5_sha = git_revision(REPO_ROOT / "gem5")
    outer_sha = git_revision(REPO_ROOT)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    run_tag = args.run_tag or f"micro_{args.suite}_{gem5_sha}_{timestamp}"
    out_root = REPO_ROOT / "m5out" / run_tag
    result_root = REPO_ROOT / "results" / "runs" / run_tag
    metadata_path = result_root / "run_manifest.json"

    if out_root.exists() or result_root.exists():
        parser.error(
            f"run tag already exists: {run_tag}; use --run-tag with a new name"
        )

    binary_sha = "DRY_RUN"
    if binary.is_file():
        binary_sha = sha256_file(binary)
    metadata = {
        "schema_version": 1,
        "run_tag": run_tag,
        "suite": args.suite,
        "host": socket.gethostname(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "outer_git_sha": outer_sha,
        "gem5_git_sha": gem5_sha,
        "binary": str(binary),
        "binary_sha256": binary_sha,
        "micro_source": str(MICRO_SOURCE),
        "micro_source_sha256": sha256_file(MICRO_SOURCE),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "windows": manifest["windows"],
        "configs": manifest["configs"],
        "workload_names": [item["name"] for item in workloads],
        "cells": [],
    }

    if not os.environ.get("SLURM_JOB_ID"):
        print("WARNING: SLURM_JOB_ID is unset; run measured cells on a compute node.")

    for workload in workloads:
        for config in manifest["configs"]:
            outdir = out_root / workload["name"] / config
            log_path = result_root / f"{workload['name']}_{config}.log"
            command = build_command(
                GEM5, RUN_SE, binary, outdir, config, workload,
                manifest["windows"],
            )
            cell = {
                "workload": workload["name"],
                "mode": workload["mode"],
                "role": workload["role"],
                "expect_prefetch": workload["expect_prefetch"],
                "config": config,
                "options": workload_options(workload),
                "outdir": str(outdir),
                "log": str(log_path),
                "command": command,
                "returncode": None,
            }
            metadata["cells"].append(cell)
            print(f"[micro-g0] {workload['name']} {config}")
            print("[micro-g0] command:", shlex.join(command))

            if args.dry_run:
                cell["returncode"] = "DRY_RUN"
                continue

            outdir.mkdir(parents=True, exist_ok=False)
            cell["returncode"] = run_logged(command, log_path)
            save_metadata(metadata_path, metadata)
            print(f"[micro-g0] exit={cell['returncode']} out={outdir}")
            if cell["returncode"] != 0:
                print(f"FAIL: stop after {workload['name']} {config}")
                return cell["returncode"] or 1

    if args.dry_run:
        print(json.dumps(metadata, indent=2, sort_keys=True))
        return 0

    save_metadata(metadata_path, metadata)
    print(f"[micro-g0] run manifest: {metadata_path}")
    if args.suite != "g0":
        print(
            f"[micro-g0] suite {args.suite!r} completed; "
            "no Gate-G0 verdict applies"
        )
        return 0
    analyzer = subprocess.run([sys.executable, str(ANALYZER), str(metadata_path)])
    return analyzer.returncode


if __name__ == "__main__":
    sys.exit(main())
