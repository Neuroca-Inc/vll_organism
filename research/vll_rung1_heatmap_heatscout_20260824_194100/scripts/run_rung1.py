#!/usr/bin/env python3
"""Run VLL Rung 1A: HeatMap + HeatScout only."""
from __future__ import annotations
import argparse
import csv
import gzip
import json
import math
import os
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode = True
from statistics import mean, median

SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = SCRIPT_DIR.parent
RESEARCH_DIR = PACKAGE_ROOT.parent
REPO_ROOT = RESEARCH_DIR.parent
for p in (str(REPO_ROOT), str(SCRIPT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from rung1_figures import attention_deltas, semantic_secondary, signal_gate, synthetic_choice
from rung1_io import (
    atomic_json, backup_sqlite, db_integrity, hash_tree, ids_for_source, load_frozen_corpus,
    resolve_source, sha256_file, utc_stamp, write_csv, clean_template,
)
from rung1_mechanism import (
    ConnectomeAdapter, PermutedHeatView, ZeroHeatView, build_heat_field,
    heatmap_signal_for_target, make_permutation, matched_path_metrics,
    run_heat_scout, synthetic_choice_gate,
)
from rung1_stats import holm_adjust, sign_flip_p_one_sided


def log_line(logf, text: str = "") -> None:
    print(text, flush=True)
    logf.write(text + "\n")
    logf.flush()


def run_tests(logf) -> None:
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", str(SCRIPT_DIR / "tests"), "-v"]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log_line(logf, proc.stdout.rstrip())
    if proc.returncode != 0:
        raise RuntimeError("package self-tests failed")


def source_class(corpus, cid: str, origin: str, related: set[str]) -> str:
    sources = corpus.source_sets.get(cid, frozenset())
    if origin in sources:
        return "origin"
    if related.intersection(sources):
        return "related"
    return "other_foreign"


def finite_mean(values):
    xs = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return mean(xs) if xs else None


def render_results(path: Path, result: dict) -> None:
    lines = [
        "# Rung 1A Results: HeatMap + HeatScout",
        "",
        f"Run: `{result['run_stamp']}`",
        f"Verdict: **{result['verdict']}**",
        "",
        "## What this actually tests",
        "",
        "Whether correctly situated, event-folded HeatMap information lets the native HeatScout choose locally hotter V0 destinations than equal-budget blind and heat-shuffled counterfactuals.",
        "",
        "## Decisive gates",
        "",
        f"- Synthetic choice-law gate: **{'PASS' if result['synthetic_gate']['pass'] else 'FAIL'}** (max abs error {result['synthetic_gate']['max_abs_error']:.6f}, tolerance {result['synthetic_gate']['tolerance']:.6f})",
        f"- Frozen DB unchanged: **{'PASS' if result['db_unchanged'] else 'FAIL'}**",
    ]
    for name, row in result["directions"].items():
        lines.extend([
            f"- `{name}` HeatMap signal gate: **{'PASS' if row['signal_gate_pass'] else 'FAIL'}** (median rho={row['signal_median_spearman']}, informative={row['signal_informative_fraction']:.3f})",
            f"- `{name}` eligible target coverage: {row['eligible_target_fraction']:.3f}",
            f"- `{name}` real-blind median delta: {row['delta_real_blind_median']}",
            f"- `{name}` real-shuffled median delta: {row['delta_real_shuffled_median']}",
        ])
    lines.extend(["", "## Multiplicity-controlled target-level tests", ""])
    for name, row in sorted(result["hypothesis_tests"].items()):
        lines.append(f"- `{name}`: median={row['median_delta']:.9g}, p={row['p']:.9g}, Holm={row['holm_p']:.9g}")
    lines.extend([
        "", "## Secondary characterization", "",
        "Semantic related-source visitation is recorded but is not an admission criterion for HeatScout.",
        "",
        "## Scope lock", "",
        "This package contains HeatMap + HeatScout only. It does not add TrailMap, another walker, topology mutation, CF04, or plasticity.",
        "",
        "## Files", "",
        "See `analysis_data/<run>/RUN_MANIFEST.json` for exact output provenance and hashes.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_findings(result: dict, results_doc: Path) -> None:
    text = [
        "# FINDINGS",
        "",
        f"Latest run: `{result['run_stamp']}`",
        f"Verdict: **{result['verdict']}**",
        "",
        "The only admitted mechanism in this package is HeatMap + HeatScout.",
        "",
        f"Detailed result: `docs/{results_doc.name}`",
        "",
        "No next mechanism is selected here. MCR adjudication follows this result.",
    ]
    (PACKAGE_ROOT / "FINDINGS.md").write_text("\n".join(text) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="VLL Rung 1A HeatMap + HeatScout experiment")
    ap.add_argument("--db", default=str(REPO_ROOT / "organism.db"))
    ap.add_argument("--quick", action="store_true", help="smoke/exploratory run; cannot earn decisive PASS")
    ap.add_argument("--skip-tests", action="store_true")
    args = ap.parse_args()

    plan = json.loads((PACKAGE_ROOT / "EXPERIMENT_PLAN.json").read_text(encoding="utf-8"))
    if args.quick:
        plan = json.loads(json.dumps(plan))
        # Keep the cheap synthetic instrument gate at full precision.
        plan["static_attention"]["trials_per_target"] = 50
        plan["static_attention"]["sign_flip_controls"] = 2000

    stamp = utc_stamp()
    run_data = PACKAGE_ROOT / "analysis_data" / stamp
    run_figs = PACKAGE_ROOT / "figures" / stamp
    run_data.mkdir(parents=True, exist_ok=False)
    run_figs.mkdir(parents=True, exist_ok=False)
    log_path = PACKAGE_ROOT / "trace_logs" / f"{stamp}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as logf:
        log_line(logf, "VLL RUNG 1A — HEATMAP + HEATSCOUT")
        log_line(logf, f"package: {PACKAGE_ROOT}")
        log_line(logf, f"repo: {REPO_ROOT}")
        log_line(logf, f"mode: {'QUICK/EXPLORATORY' if args.quick else 'FULL/DECISIVE'}")
        if not args.skip_tests:
            run_tests(logf)

        live_db = Path(args.db).resolve()
        if not live_db.is_file():
            raise RuntimeError(f"database not found: {live_db}")
        frozen_db = run_data / "frozen_organism.db"
        backup_sqlite(live_db, frozen_db)
        integrity = db_integrity(frozen_db)
        if integrity["quick_check"] != ["ok"] or integrity["foreign_key_violations"]:
            raise RuntimeError(f"frozen DB integrity failed: {integrity}")
        db_hash_before = sha256_file(frozen_db)

        corpus = load_frozen_corpus(frozen_db)
        connectome = ConnectomeAdapter(corpus)
        template = clean_template(corpus)
        log_line(logf, f"snapshot tick={corpus.snapshot_tick} chunks={len(corpus.ids)} nodes={corpus.graph.node_count()} edges={corpus.graph.edge_count()}")
        log_line(logf, f"frozen db sha256={db_hash_before}")

        synthetic_rows, synthetic_gate_result = synthetic_choice_gate(plan)
        write_csv(run_data / "synthetic_choice_gate.csv", synthetic_rows,
                  ["delta_heat", "predicted_p_A", "observed_p_A", "abs_error", "trials"])
        synthetic_choice(synthetic_rows, run_figs / "synthetic_choice_law.svg")
        log_line(logf, f"synthetic gate: {synthetic_gate_result}")

        signal_rows = []
        target_rows = []
        trial_path = run_data / "walker_trials.csv.gz"
        trial_fields = [
            "direction", "target_id", "trial", "matched_edges",
            "blind_mean_heat", "real_mean_heat", "shuffled_mean_heat",
            "blind_unique_heat_per_edge", "real_unique_heat_per_edge", "shuffled_unique_heat_per_edge",
            "blind_related_fraction", "real_related_fraction", "shuffled_related_fraction",
        ]
        hypothesis_raw = {}
        direction_results = {}

        with gzip.open(trial_path, "wt", encoding="utf-8", newline="") as gf:
            writer = csv.DictWriter(gf, fieldnames=trial_fields)
            writer.writeheader()
            for di, spec in enumerate(plan["source_pairs"]):
                dname = spec["name"]
                origin = resolve_source(corpus, spec["origin"])
                related = {resolve_source(corpus, s) for s in spec["related"]}
                targets = ids_for_source(corpus, origin)
                if not targets:
                    raise RuntimeError(f"no targets for source direction {dname}")
                d_target_rows = []
                d_signal = []
                log_line(logf, f"direction {dname}: targets={len(targets)}")

                for ti, target_id in enumerate(targets):
                    target_idx = connectome.id_to_idx[target_id]
                    field = build_heat_field(template, corpus, connectome, target_id, plan)
                    sg = heatmap_signal_for_target(target_idx, connectome, field)
                    signal_row = {
                        "direction": dname, "target_id": target_id,
                        "neighbor_count": sg["neighbor_count"], "spearman": sg["spearman"],
                        "map_spread": sg["map_spread"], "informative": int(sg["informative"]),
                        "heatmap_event_count": field["event_count"], "field_total_heat": field["total_heat"],
                        "max_active_nodes": field["max_active"],
                    }
                    signal_rows.append(signal_row)
                    d_signal.append(signal_row)

                    arm_primary = {"blind": [], "real": [], "shuffled": []}
                    arm_unique = {"blind": [], "real": [], "shuffled": []}
                    arm_sem = {"blind": [], "real": [], "shuffled": []}
                    valid_trials = 0
                    matched_edges_accum = []
                    ntrials = int(plan["static_attention"]["trials_per_target"])
                    for trial in range(ntrials):
                        seed = int(plan["seed"]) + di * 1_000_000_000 + ti * 1_000_000 + trial
                        permutation = make_permutation(connectome.N, seed ^ 0x5EED5EED)
                        paths = {
                            "blind": run_heat_scout(connectome, target_idx, ZeroHeatView(), plan, seed),
                            "real": run_heat_scout(connectome, target_idx, field["heat_view"], plan, seed),
                            "shuffled": run_heat_scout(connectome, target_idx, PermutedHeatView(field["heat_view"], permutation), plan, seed),
                        }
                        metrics = matched_path_metrics(paths, field["authoritative_heat"], corpus, connectome, origin, related)
                        matched = metrics["real"]["matched_edges"]
                        if matched > 0:
                            valid_trials += 1
                            matched_edges_accum.append(matched)
                            for arm in arm_primary:
                                arm_primary[arm].append(metrics[arm]["mean_destination_heat"])
                                arm_unique[arm].append(metrics[arm]["unique_heat_per_edge"])
                                if metrics[arm]["related_fraction_foreign"] is not None:
                                    arm_sem[arm].append(metrics[arm]["related_fraction_foreign"])
                        writer.writerow({
                            "direction": dname, "target_id": target_id, "trial": trial,
                            "matched_edges": matched,
                            "blind_mean_heat": metrics["blind"]["mean_destination_heat"],
                            "real_mean_heat": metrics["real"]["mean_destination_heat"],
                            "shuffled_mean_heat": metrics["shuffled"]["mean_destination_heat"],
                            "blind_unique_heat_per_edge": metrics["blind"]["unique_heat_per_edge"],
                            "real_unique_heat_per_edge": metrics["real"]["unique_heat_per_edge"],
                            "shuffled_unique_heat_per_edge": metrics["shuffled"]["unique_heat_per_edge"],
                            "blind_related_fraction": metrics["blind"]["related_fraction_foreign"],
                            "real_related_fraction": metrics["real"]["related_fraction_foreign"],
                            "shuffled_related_fraction": metrics["shuffled"]["related_fraction_foreign"],
                        })

                    valid_fraction = valid_trials / ntrials
                    b = finite_mean(arm_primary["blind"])
                    r = finite_mean(arm_primary["real"])
                    s = finite_mean(arm_primary["shuffled"])
                    sem_b = finite_mean(arm_sem["blind"])
                    sem_r = finite_mean(arm_sem["real"])
                    sem_s = finite_mean(arm_sem["shuffled"])
                    eligible = (
                        valid_fraction >= float(plan["static_attention"]["min_valid_trial_fraction_per_target"])
                        and b is not None and r is not None and s is not None
                    )
                    tr = {
                        "direction": dname, "target_id": target_id, "eligible": int(eligible),
                        "valid_trial_fraction": valid_fraction,
                        "mean_matched_edges": finite_mean(matched_edges_accum),
                        "blind_mean_heat": b, "real_mean_heat": r, "shuffled_mean_heat": s,
                        "delta_real_blind": (r - b) if eligible else None,
                        "delta_real_shuffled": (r - s) if eligible else None,
                        "blind_unique_heat_per_edge": finite_mean(arm_unique["blind"]),
                        "real_unique_heat_per_edge": finite_mean(arm_unique["real"]),
                        "shuffled_unique_heat_per_edge": finite_mean(arm_unique["shuffled"]),
                        "blind_related_fraction": sem_b, "real_related_fraction": sem_r,
                        "shuffled_related_fraction": sem_s,
                        "semantic_delta_real_blind": (sem_r - sem_b) if sem_r is not None and sem_b is not None else None,
                        "semantic_delta_real_shuffled": (sem_r - sem_s) if sem_r is not None and sem_s is not None else None,
                    }
                    target_rows.append(tr)
                    d_target_rows.append(tr)
                    log_line(logf, f"  [{ti+1:02d}/{len(targets):02d}] {target_id} valid={valid_fraction:.3f} delta_rb={tr['delta_real_blind']} delta_rs={tr['delta_real_shuffled']}")

                eligible_rows = [r for r in d_target_rows if r["eligible"]]
                eligible_fraction = len(eligible_rows) / len(d_target_rows)
                rhos = [float(r["spearman"]) for r in d_signal if r["spearman"] is not None]
                informative_fraction = sum(int(r["informative"]) for r in d_signal) / len(d_signal)
                med_rho = median(rhos) if rhos else None
                signal_pass = (
                    med_rho is not None
                    and med_rho >= float(plan["signal_gate"]["median_spearman_min"])
                    and informative_fraction >= float(plan["signal_gate"]["informative_target_fraction_min"])
                )
                rb = [float(r["delta_real_blind"]) for r in eligible_rows]
                rs = [float(r["delta_real_shuffled"]) for r in eligible_rows]
                controls = int(plan["static_attention"]["sign_flip_controls"])
                p_rb, med_rb, exc_rb = sign_flip_p_one_sided(rb, controls=controls, seed=int(plan["seed"]) + di * 17 + 1)
                p_rs, med_rs, exc_rs = sign_flip_p_one_sided(rs, controls=controls, seed=int(plan["seed"]) + di * 17 + 2)
                hypothesis_raw[f"{dname}:real_vs_blind"] = {"p": p_rb, "median_delta": med_rb, "exceed": exc_rb}
                hypothesis_raw[f"{dname}:real_vs_shuffled"] = {"p": p_rs, "median_delta": med_rs, "exceed": exc_rs}
                direction_results[dname] = {
                    "targets": len(d_target_rows), "eligible_targets": len(eligible_rows),
                    "eligible_target_fraction": eligible_fraction,
                    "signal_median_spearman": med_rho,
                    "signal_informative_fraction": informative_fraction,
                    "signal_gate_pass": signal_pass,
                    "delta_real_blind_median": med_rb,
                    "delta_real_shuffled_median": med_rs,
                }

        write_csv(run_data / "heatmap_signal_targets.csv", signal_rows,
                  ["direction", "target_id", "neighbor_count", "spearman", "map_spread", "informative", "heatmap_event_count", "field_total_heat", "max_active_nodes"])
        write_csv(run_data / "target_summary.csv", target_rows,
                  ["direction", "target_id", "eligible", "valid_trial_fraction", "mean_matched_edges",
                   "blind_mean_heat", "real_mean_heat", "shuffled_mean_heat", "delta_real_blind", "delta_real_shuffled",
                   "blind_unique_heat_per_edge", "real_unique_heat_per_edge", "shuffled_unique_heat_per_edge",
                   "blind_related_fraction", "real_related_fraction", "shuffled_related_fraction",
                   "semantic_delta_real_blind", "semantic_delta_real_shuffled"])

        signal_gate(signal_rows, run_figs / "heatmap_signal_gate.svg")
        attention_deltas([r for r in target_rows if r["eligible"]], run_figs / "static_attention_gain.svg")
        semantic_secondary(target_rows, run_figs / "semantic_secondary.svg")

        holm = holm_adjust((name, row["p"]) for name, row in hypothesis_raw.items())
        tests = {}
        for name, row in hypothesis_raw.items():
            tests[name] = {**row, "holm_p": holm[name]}

        db_hash_after = sha256_file(frozen_db)
        db_unchanged = db_hash_before == db_hash_after
        min_cov = float(plan["static_attention"]["min_eligible_target_fraction_per_direction"])
        alpha = float(plan["static_attention"]["holm_family_alpha"])
        decisive_pass = (
            synthetic_gate_result["pass"]
            and db_unchanged
            and all(r["signal_gate_pass"] for r in direction_results.values())
            and all(r["eligible_target_fraction"] >= min_cov for r in direction_results.values())
            and all(row["median_delta"] > 0.0 and row["holm_p"] <= alpha for row in tests.values())
        )
        verdict = "EXPLORATORY" if args.quick else ("PASS" if decisive_pass else "FAIL")

        result = {
            "schema_version": 1, "run_stamp": stamp, "experiment_id": plan["experiment_id"],
            "mode": "quick" if args.quick else "full", "verdict": verdict,
            "synthetic_gate": synthetic_gate_result, "directions": direction_results,
            "hypothesis_tests": tests, "db_unchanged": db_unchanged,
            "frozen_db_sha256_before": db_hash_before, "frozen_db_sha256_after": db_hash_after,
            "snapshot": {
                "tick": corpus.snapshot_tick, "chunks": len(corpus.ids),
                "nodes": corpus.graph.node_count(), "edges": corpus.graph.edge_count(),
                "meta": corpus.meta, "integrity": integrity,
            },
            "scope_lock": plan["scope_lock"], "claim": plan["claim"], "nonclaims": plan["nonclaims"],
        }
        atomic_json(run_data / "gate_results.json", result)

        results_doc = PACKAGE_ROOT / "docs" / f"{stamp}_RESULTS.md"
        render_results(results_doc, result)
        update_findings(result, results_doc)

        log_line(logf, "")
        log_line(logf, f"VERDICT: {verdict}")
        for name, row in tests.items():
            log_line(logf, f"{name}: median={row['median_delta']:.9g} p={row['p']:.9g} holm={row['holm_p']:.9g}")
        log_line(logf, f"results: {results_doc}")
        log_line(logf, f"run data: {run_data}")
        log_line(logf, "STOP: no next mechanism is admitted by this package.")

        manifest = {
            "schema_version": 1, "run_stamp": stamp,
            "command": " ".join(sys.argv),
            "python": sys.version,
            "repo_root": str(REPO_ROOT), "source_db": str(live_db),
            "plan_sha256": sha256_file(PACKAGE_ROOT / "EXPERIMENT_PLAN.json"),
            "critical_repo_files": [],
        }
        for rel in ("pyproject.toml", "vll_organism/dynamics.py", "vll_organism/graph.py", "vll_organism/storage.py"):
            p = REPO_ROOT / rel
            manifest["critical_repo_files"].append({"path": rel, "exists": p.is_file(), "sha256": sha256_file(p) if p.is_file() else None})
        manifest["run_files"] = hash_tree(PACKAGE_ROOT, exclude_names={"RUN_MANIFEST.json", "RUN_SHA256SUMS.txt"})
        atomic_json(run_data / "RUN_MANIFEST.json", manifest)
        sums = [f"{r['sha256']}  {r['path']}" for r in hash_tree(PACKAGE_ROOT, exclude_names={"RUN_SHA256SUMS.txt"})]
        (run_data / "RUN_SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")



if __name__ == "__main__":
    main()
