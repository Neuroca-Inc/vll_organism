#!/usr/bin/env python3
"""Checkpointed orchestrator for a frozen Rung-0 experiment plan."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil

from rung0_common import atomic_json, sha256_file


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_plan(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not data.get("experiment_id"):
        raise ValueError("plan requires experiment_id")
    if not data.get("pairs"):
        raise ValueError("plan requires at least one pair")
    return data


def stream_stage(stage: str, command: list[str], log_path: Path, cwd: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\n=== {stage} ===")
    print("command:", " ".join(command))
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log.write(line)
        return process.wait()



def package_hashes(package: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(package.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = str(path.relative_to(package))
        hashes[rel] = sha256_file(path)
    return hashes


def snapshot_provenance(run_dir: Path, plan_path: Path, package: Path) -> None:
    dest = run_dir / "provenance"
    if dest.exists():
        return
    dest.mkdir(parents=True)
    shutil.copy2(plan_path, dest / "PLAN.json")
    code = dest / "battery_code"
    for path in sorted(package.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        target = code / path.relative_to(package)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def final_manifest(run_dir: Path) -> dict:
    rows = []
    manifest_path = run_dir / "RUN_MANIFEST.json"
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path == manifest_path:
            continue
        rows.append({
            "path": str(path.relative_to(run_dir)),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return {"schema_version": 1, "created_at_utc": now(), "files": rows}

def stage_done(ledger: dict, stage: str) -> bool:
    return ledger.get("stages", {}).get(stage, {}).get("status") == "PASS"


def run_stage(
    ledger_path: Path, ledger: dict, stage: str, command: list[str], cwd: Path, *,
    resume: bool, cleanup_paths: tuple[Path, ...] = (),
) -> None:
    if resume and stage_done(ledger, stage):
        print(f"SKIP completed stage: {stage}")
        return
    for path in cleanup_paths:
        resolved = path.resolve()
        run_root = ledger_path.parent.resolve()
        if resolved == run_root or run_root not in resolved.parents:
            raise RuntimeError(f"refusing cleanup outside run directory: {resolved}")
        if resolved.is_dir():
            shutil.rmtree(resolved)
        elif resolved.exists():
            resolved.unlink()
    entry = {"status": "RUNNING", "started_at_utc": now(), "command": command}
    ledger.setdefault("stages", {})[stage] = entry
    atomic_json(ledger_path, ledger)
    rc = stream_stage(stage, command, ledger_path.parent / "logs" / f"{stage}.log", cwd)
    entry["finished_at_utc"] = now()
    entry["returncode"] = rc
    entry["status"] = "PASS" if rc == 0 else "FAIL"
    atomic_json(ledger_path, ledger)
    if rc != 0:
        raise SystemExit(f"stage {stage!r} failed with exit code {rc}; prior checkpoints are preserved")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run checkpointed VLL Rung-0 research plan")
    ap.add_argument("--db", required=True, help="live DB; only the freeze stage reads it")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    package = repo / "research" / "rung0_battery"
    if not package.is_dir():
        raise SystemExit(f"battery package not found: {package}")
    plan = load_plan(args.plan)
    plan_path = Path(args.plan).resolve()
    plan_hash = sha256_file(plan_path)
    code_hashes = package_hashes(package)
    run_dir = Path(args.out).resolve()
    if run_dir.exists() and not args.resume:
        raise SystemExit(f"output directory exists; use --resume to continue: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = run_dir / "RUN_LEDGER.json"
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if ledger.get("experiment_id") != plan["experiment_id"]:
            raise SystemExit("resume refused: experiment_id differs from existing run ledger")
        if ledger.get("plan_sha256") != plan_hash:
            raise SystemExit("resume refused: plan changed after the run began; start a successor run")
        if ledger.get("package_hashes") != code_hashes:
            raise SystemExit("resume refused: battery code changed after the run began; start a successor run")
        if ledger.get("live_db_path") != str(Path(args.db).resolve()):
            raise SystemExit("resume refused: live DB path differs from the original run")
    else:
        ledger = {
            "schema_version": 1,
            "experiment_id": plan["experiment_id"],
            "created_at_utc": now(),
            "plan_path": str(plan_path),
            "plan_sha256": plan_hash,
            "package_hashes": code_hashes,
            "live_db_path": str(Path(args.db).resolve()),
            "stages": {},
        }
        atomic_json(ledger_path, ledger)

    snapshot_provenance(run_dir, plan_path, package)

    py = sys.executable
    env_path = os.environ.get("PYTHONPATH", "")
    required_paths = [str(repo), str(package)]
    os.environ["PYTHONPATH"] = os.pathsep.join(required_paths + ([env_path] if env_path else []))

    if plan.get("run_tests", True):
        run_stage(
            ledger_path, ledger, "00_tests",
            [py, "-m", "pytest", "-q", "tests", "research/rung0_battery/tests"],
            repo, resume=args.resume,
        )

    snapshot_dir = run_dir / "snapshot"
    run_stage(
        ledger_path, ledger, "01_freeze",
        [
            py, str(package / "freeze_snapshot.py"), "--db", str(Path(args.db).resolve()),
            "--out", str(snapshot_dir), "--repo-root", str(repo),
            "--note", plan.get("snapshot_note", plan["experiment_id"]),
        ],
        repo, resume=args.resume, cleanup_paths=(snapshot_dir,),
    )
    frozen_db = snapshot_dir / "organism.db"
    if not frozen_db.is_file():
        raise SystemExit(f"frozen DB missing after freeze stage: {frozen_db}")

    defaults = plan.get("pair_defaults", {})
    for index, pair in enumerate(plan["pairs"], start=2):
        stage = f"{index:02d}_pair_{pair['id']}"
        pair_out = run_dir / f"pair_{pair['id']}"
        command = [
            py, str(package / "pair_battery.py"),
            "--db", str(frozen_db),
            "--source", pair["source"],
            "--heat", str(defaults.get("heat", 10)),
            "--primary-horizon", str(defaults.get("primary_horizon", 120)),
            "--node-controls", str(defaults.get("node_controls", 5000)),
            "--omnibus-controls", str(defaults.get("omnibus_controls", 10000)),
            "--degree-controls", str(defaults.get("degree_controls", 10000)),
            "--seed", str(plan.get("seed", 20260824)),
            "--heat-robustness-tol", str(defaults.get("heat_robustness_tol", 0.02)),
            "--out", str(pair_out),
        ]
        for related in pair["related"]:
            command.extend(["--related", related])
        for horizon in defaults.get("horizons", [20, 60, 120, 240]):
            command.extend(["--horizon", str(horizon)])
        for level in defaults.get("heat_levels", [1, 10, 100]):
            command.extend(["--heat-level", str(level)])
        run_stage(
            ledger_path, ledger, stage, command, repo, resume=args.resume,
            cleanup_paths=(pair_out,),
        )

    matrix = plan.get("matrix")
    next_stage = 2 + len(plan["pairs"])
    if matrix and matrix.get("enabled", True):
        stage = f"{next_stage:02d}_source_matrix"
        command = [
            py, str(package / "source_matrix.py"), "--db", str(frozen_db),
            "--heat", str(matrix.get("heat", defaults.get("heat", 10))),
            "--ticks", str(matrix.get("ticks", defaults.get("primary_horizon", 120))),
            "--controls", str(matrix.get("controls", 5000)),
            "--seed", str(plan.get("seed", 20260824)),
            "--out", str(run_dir / "source_matrix"),
        ]
        for source in matrix["sources"]:
            command.extend(["--source", source])
        matrix_out = run_dir / "source_matrix"
        if args.resume:
            command.append("--resume")
        run_stage(
            ledger_path, ledger, stage, command, repo, resume=args.resume,
            cleanup_paths=() if args.resume else (matrix_out,),
        )
        next_stage += 1

    order = plan.get("order_sensitivity")
    if order and order.get("enabled", True):
        stage = f"{next_stage:02d}_order_sensitivity"
        command = [
            py, str(package / "order_sensitivity.py"), "--db", str(frozen_db),
            "--source", order["source"],
            "--heat", str(order.get("heat", defaults.get("heat", 10))),
            "--ticks", str(order.get("ticks", defaults.get("primary_horizon", 120))),
            "--controls", str(order.get("controls", 100)),
            "--seed", str(plan.get("seed", 20260824)),
            "--similarity-threshold", str(order.get("similarity_threshold", 0.55)),
            "--similarity-candidate-cap", str(order.get("similarity_candidate_cap", 200)),
            "--max-out-degree", str(order.get("max_out_degree", 6)),
            "--similarity-cache-size", str(order.get("similarity_cache_size", 50000)),
            "--out", str(run_dir / "order_sensitivity"),
        ]
        for related in order["related"]:
            command.extend(["--related", related])
        order_out = run_dir / "order_sensitivity"
        if args.resume:
            command.append("--resume")
        run_stage(
            ledger_path, ledger, stage, command, repo, resume=args.resume,
            cleanup_paths=() if args.resume else (order_out,),
        )

    ledger["finished_at_utc"] = now()
    ledger["status"] = "PASS"
    atomic_json(ledger_path, ledger)
    atomic_json(run_dir / "RUN_MANIFEST.json", final_manifest(run_dir))
    print("\nRUNG0_PLAN_COMPLETE")
    print(f"run_dir={run_dir}")
    print(f"ledger={ledger_path}")


if __name__ == "__main__":
    main()
