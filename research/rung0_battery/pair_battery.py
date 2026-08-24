#!/usr/bin/env python3
"""High-rigor pairwise Rung-0 routing battery over a frozen VLL database."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics

from rung0_common import (
    atomic_json, base_name, clean_template, flat_weight_provider,
    lesion_target_related_provider, load_frozen_corpus,
    neighbor_profile, preview_text, real_fraction, resolve_source, run_heat, source_auc_real,
    source_class_sets, source_inventory, shortest_hops, validate_group_provenance,
    with_diffusion, write_csv,
)
from rung0_stats import benjamini_hochberg, holm_bonferroni, null_summary
from rung0_controls import (
    degree_match_coverage, fidelity_sentinel, global_control_matrix,
    omnibus_from_matrices, per_target_controls,
)


def source_order_confounded(inventory, origin_source, related_sources):
    by_source = {row["source"]: row for row in inventory}
    origin = by_source[origin_source]
    overlaps = []
    for source in related_sources:
        related = by_source[source]
        overlaps.append(
            max(origin["created_rank_min"], related["created_rank_min"])
            <= min(origin["created_rank_max"], related["created_rank_max"])
        )
    return not any(overlaps)


def main() -> None:
    ap = argparse.ArgumentParser(description="Frozen Rung-0 pairwise routing battery")
    ap.add_argument("--db", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--related", action="append", required=True)
    ap.add_argument("--heat", type=float, default=10.0)
    ap.add_argument("--horizon", type=int, action="append", default=[])
    ap.add_argument("--primary-horizon", type=int, default=120)
    ap.add_argument("--heat-level", type=float, action="append", default=[])
    ap.add_argument("--heat-robustness-tol", type=float, default=0.02)
    ap.add_argument("--node-controls", type=int, default=5000)
    ap.add_argument("--omnibus-controls", type=int, default=10000)
    ap.add_argument("--degree-controls", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=20260824)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.node_controls < 1000 or args.omnibus_controls < 1000:
        raise SystemExit("high-rigor battery requires at least 1000 node and omnibus controls")
    if args.degree_controls < 0:
        raise SystemExit("--degree-controls must be >= 0")
    horizons = sorted(set(args.horizon or [20, 60, 120, 240]) | {args.primary_horizon})
    heat_levels = sorted(set(args.heat_level or [1.0, args.heat, 100.0]) | {args.heat})
    if any(h < 1 for h in horizons) or args.heat <= 0 or any(h <= 0 for h in heat_levels):
        raise SystemExit("horizons and heat levels must be positive")

    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit(f"refusing to overwrite output directory: {out}")
    out.mkdir(parents=True)

    corpus = load_frozen_corpus(args.db)
    origin_source = resolve_source(corpus, args.source)
    related_sources = [resolve_source(corpus, x) for x in args.related]
    if origin_source in related_sources:
        raise SystemExit("origin source cannot also be designated related")
    validate_group_provenance(corpus, origin_source, related_sources)
    origin_ids, related_ids = source_class_sets(corpus, origin_source, related_sources)
    if not origin_ids or not related_ids:
        raise SystemExit("origin and related classes must both contain chunks")

    template = clean_template(corpus)
    target_runs = {}
    rows = []
    checkpoint_ticks = sorted({1, 2, 5, 10, 20, 40, 60, 90, args.primary_horizon, max(horizons)})

    print("RUNG-0 PAIRWISE ROUTING BATTERY")
    print(f"db={corpus.db_path} tick={corpus.snapshot_tick} nodes={len(corpus.ids)} edges={corpus.graph.edge_count()}")
    print(f"source={base_name(origin_source)} chunks={len(origin_ids)}")
    print(f"related={[base_name(s) for s in related_sources]} chunks={len(related_ids)}")
    print(f"horizons={horizons} primary={args.primary_horizon} heat={args.heat}")

    for index, target in enumerate(sorted(origin_ids), 1):
        run = run_heat(template, corpus, target, args.heat, horizons, checkpoint_ticks=checkpoint_ticks)
        target_runs[target] = run
        auc = run.node_auc_by_horizon[args.primary_horizon]
        real, origin_auc, rel_auc, other_auc = real_fraction(auc, origin_ids, related_ids)
        profile = neighbor_profile(corpus, target, origin_ids, related_ids)
        hops = shortest_hops(corpus, target, related_ids)
        source_auc = source_auc_real(corpus, auc, origin_source)
        top_source = max(source_auc.items(), key=lambda kv: kv[1])[0] if source_auc else None
        controls = per_target_controls(
            corpus, auc, target, origin_ids, related_ids, args.node_controls, args.seed
        ) if real is not None else []
        stats = null_summary(real, controls)
        row = {
            "target": target,
            "hops": hops,
            **profile,
            "real_fraction": real,
            "origin_auc": origin_auc,
            "related_auc": rel_auc,
            "other_foreign_auc": other_auc,
            **stats,
            "top_foreign_source": None if top_source is None else base_name(top_source),
            "preview": preview_text(corpus, target),
            "status": "UNREACHABLE" if hops is None else ("NO_FOREIGN_HEAT" if real is None else "OK"),
        }
        rows.append(row)
        real_text = "NA" if real is None else f"{real:.4f}"
        null_text = "NA" if stats["null_median"] is None else f"{stats["null_median"]:.4f}"
        pr_text = "NA" if stats["p_right"] is None else f"{stats["p_right"]:.5f}"
        print(
            f"[{index:02d}/{len(origin_ids):02d}] {target} hops={hops if hops is not None else 'NA'} "
            f"direct={profile['direct_related']}/{profile['direct_other_foreign']} "
            f"real={real_text} null={null_text} pR={pr_text} "
            f"top={base_name(top_source) if top_source else '(none)'}"
        )

    eligible = [r for r in rows if r["real_fraction"] is not None and r["hops"] is not None]
    p_right = [r["p_right"] if r in eligible else None for r in rows]
    q_bh = benjamini_hochberg(p_right)
    p_holm = holm_bonferroni(p_right)
    for row, q, holm in zip(rows, q_bh, p_holm):
        row["q_bh_right"] = q
        row["p_holm_right"] = holm
        row["pass_node_q95"] = (
            row["real_fraction"] is not None and row["null_q95"] is not None
            and row["real_fraction"] > row["null_q95"]
        )

    if not eligible:
        raise RuntimeError(
            "no source targets can reach the designated related class with measurable foreign heat"
        )

    first = eligible[0]
    fidelity = fidelity_sentinel(
        corpus, template, first["target"], target_runs[first["target"]].node_auc_by_horizon[args.primary_horizon],
        origin_ids, related_ids, args.heat, args.primary_horizon, args.seed,
    )

    target_aucs = {
        r["target"]: target_runs[r["target"]].node_auc_by_horizon[args.primary_horizon]
        for r in eligible
    }
    global_calibration = global_control_matrix(
        corpus, target_aucs, origin_ids, related_ids, args.omnibus_controls, args.seed,
        stream="calibration",
    )
    global_evaluation = global_control_matrix(
        corpus, target_aucs, origin_ids, related_ids, args.omnibus_controls, args.seed,
        stream="evaluation",
    )
    eligible_ids = {r["target"] for r in eligible}
    omnibus = [
        omnibus_from_matrices(rows, global_calibration, global_evaluation, lambda r: r["target"] in eligible_ids, "all_reachable"),
        omnibus_from_matrices(
            rows, global_calibration, global_evaluation,
            lambda r: r["target"] in eligible_ids and r["direct_related"] == 0 and (r["hops"] or 0) >= 2,
            "natural_multihop",
        ),
        omnibus_from_matrices(rows, global_calibration, global_evaluation, lambda r: r["target"] in eligible_ids and r["hops"] == 1, "one_hop"),
        omnibus_from_matrices(rows, global_calibration, global_evaluation, lambda r: r["target"] in eligible_ids and r["hops"] == 2, "two_hop"),
        omnibus_from_matrices(rows, global_calibration, global_evaluation, lambda r: r["target"] in eligible_ids and (r["hops"] or 0) >= 3, "three_plus_hop"),
    ]

    matched = None
    coverage = degree_match_coverage(corpus, origin_ids, related_ids)
    if args.degree_controls:
        degree_calibration = global_control_matrix(
            corpus, target_aucs, origin_ids, related_ids, args.degree_controls, args.seed,
            degree_matched=True, stream="calibration",
        )
        degree_evaluation = global_control_matrix(
            corpus, target_aucs, origin_ids, related_ids, args.degree_controls, args.seed,
            degree_matched=True, stream="evaluation",
        )
        matched = {
            "degree_match_movable_fraction": coverage,
            "all_reachable": omnibus_from_matrices(
                rows, degree_calibration, degree_evaluation,
                lambda r: r["target"] in eligible_ids, "degree_matched_all"
            ),
            "natural_multihop": omnibus_from_matrices(
                rows, degree_calibration, degree_evaluation,
                lambda r: r["target"] in eligible_ids and r["direct_related"] == 0 and (r["hops"] or 0) >= 2,
                "degree_matched_multihop",
            ),
        }

    horizon_summary = []
    for horizon in horizons:
        fractions = []
        for row in eligible:
            auc = target_runs[row["target"]].node_auc_by_horizon[horizon]
            frac, *_ = real_fraction(auc, origin_ids, related_ids)
            if frac is not None:
                fractions.append(frac)
        horizon_summary.append({
            "horizon": horizon,
            "targets": len(fractions),
            "median_real_fraction": statistics.median(fractions) if fractions else None,
        })

    heat_rows = []
    max_deviation = 0.0
    for row in eligible:
        fractions = {}
        for level in heat_levels:
            run = run_heat(template, corpus, row["target"], level, [args.primary_horizon])
            frac, *_ = real_fraction(run.node_auc_by_horizon[args.primary_horizon], origin_ids, related_ids)
            fractions[str(level)] = frac
        base = fractions[str(args.heat)]
        deviations = [abs(v - base) for v in fractions.values() if v is not None and base is not None]
        dev = max(deviations or [0.0])
        max_deviation = max(max_deviation, dev)
        heat_rows.append({"target": row["target"], "max_abs_deviation": dev, **fractions})

    zero_template = clean_template(corpus, config=with_diffusion(corpus.dynamics_config, 0.0))
    zero_failures = []
    for target in origin_ids:
        run = run_heat(zero_template, corpus, target, args.heat, [args.primary_horizon])
        _frac, _origin, rel, other = real_fraction(run.node_auc_by_horizon[args.primary_horizon], origin_ids, related_ids)
        if abs(rel) + abs(other) > 1e-12:
            zero_failures.append({"target": target, "foreign_auc": rel + other})
    if zero_failures:
        raise RuntimeError(f"zero-diffusion negative control failed: {zero_failures[:5]}")

    flat_rows = []
    lesion_rows = []
    for row in eligible:
        target = row["target"]
        flat = run_heat(template, corpus, target, args.heat, [args.primary_horizon], provider=flat_weight_provider(corpus))
        flat_frac, *_ = real_fraction(flat.node_auc_by_horizon[args.primary_horizon], origin_ids, related_ids)
        flat_rows.append({"target": target, "real_fraction": row["real_fraction"], "flat_weight_fraction": flat_frac})
        if row["direct_related"] > 0:
            provider = lesion_target_related_provider(corpus, target, related_ids)
            lesion = run_heat(template, corpus, target, args.heat, [args.primary_horizon], provider=provider)
            lesion_frac, *_ = real_fraction(lesion.node_auc_by_horizon[args.primary_horizon], origin_ids, related_ids)
            lesion_rows.append({
                "target": target,
                "original_fraction": row["real_fraction"],
                "lesioned_fraction": lesion_frac,
                "lesioned_shortest_hops": shortest_hops(corpus, target, related_ids, provider=provider),
            })

    budget_diagnostics = {
        "active_budget": corpus.dynamics_config.active_budget,
        "max_active_nodes_any_target": max(run.max_active_nodes for run in target_runs.values()),
        "targets_with_budget_saturation": sum(run.budget_saturation_ticks > 0 for run in target_runs.values()),
        "total_budget_saturation_ticks": sum(run.budget_saturation_ticks for run in target_runs.values()),
    }

    inventory = source_inventory(corpus)
    confound = {
        "source_creation_order_nonoverlap": source_order_confounded(inventory, origin_source, related_sources),
        "interpretation": "If true, this snapshot cannot by itself separate semantic organization from document ingestion-order effects.",
    }

    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "db": corpus.db_path,
        "snapshot_tick": corpus.snapshot_tick,
        "source": origin_source,
        "related_sources": related_sources,
        "parameters": {
            "heat": args.heat, "horizons": horizons, "primary_horizon": args.primary_horizon,
            "heat_levels": heat_levels, "heat_robustness_tol": args.heat_robustness_tol,
            "node_controls": args.node_controls, "omnibus_controls": args.omnibus_controls,
            "degree_controls": args.degree_controls, "seed": args.seed,
            "omnibus_null_streams": "independent calibration + evaluation",
        },
        "fidelity_sentinel": fidelity,
        "zero_diffusion_negative_control": {"pass": True, "targets": len(origin_ids)},
        "active_budget_diagnostics": budget_diagnostics,
        "heat_scale_robustness": {
            "max_abs_fraction_deviation": max_deviation,
            "tolerance": args.heat_robustness_tol,
            "pass": max_deviation <= args.heat_robustness_tol,
        },
        "node_level": {
            "eligible_targets": len(eligible),
            "node_q95_passes": sum(bool(r["pass_node_q95"]) for r in eligible),
            "bh_q_le_0_05": sum((r["q_bh_right"] or 1.0) <= 0.05 for r in eligible),
            "holm_p_le_0_05": sum((r["p_holm_right"] or 1.0) <= 0.05 for r in eligible),
            "natural_multihop_targets": sum(r["direct_related"] == 0 and (r["hops"] or 0) >= 2 for r in eligible),
            "natural_multihop_node_q95_passes": sum(
                bool(r["pass_node_q95"]) and r["direct_related"] == 0 and (r["hops"] or 0) >= 2 for r in eligible
            ),
        },
        "omnibus": omnibus,
        "degree_matched_omnibus": matched,
        "confound_audit": confound,
        "horizon_summary": horizon_summary,
        "uniform_weight_diagnostic": flat_rows,
        "direct_related_lesion_diagnostic": lesion_rows,
    }

    atomic_json(out / "results.json", {"summary": summary, "targets": rows, "heat_robustness": heat_rows})
    write_csv(out / "targets.csv", rows, [
        "target", "hops", "direct_origin", "direct_related", "direct_other_foreign",
        "direct_related_weight", "direct_other_foreign_weight", "real_fraction", "null_median",
        "null_q05", "null_q95", "delta", "effect_ratio", "p_right", "p_left", "p_two_sided",
        "q_bh_right", "p_holm_right", "pass_node_q95", "top_foreign_source", "status", "preview",
    ])
    write_csv(out / "source_inventory.csv", inventory, list(inventory[0].keys()))
    write_csv(out / "heat_robustness.csv", heat_rows, list(heat_rows[0].keys()) if heat_rows else ["target"])

    lines = [
        "# Rung-0 Pairwise Routing Battery",
        "",
        f"- Source: `{base_name(origin_source)}`",
        f"- Related: {', '.join(f'`{base_name(x)}`' for x in related_sources)}",
        f"- Frozen tick: {corpus.snapshot_tick}",
        f"- Eligible targets: {len(eligible)}",
        f"- Node q95 passes: {summary['node_level']['node_q95_passes']}/{len(eligible)}",
        f"- Natural multi-hop q95 passes: {summary['node_level']['natural_multihop_node_q95_passes']}/{summary['node_level']['natural_multihop_targets']}",
        f"- BH-FDR q<=0.05: {summary['node_level']['bh_q_le_0_05']}/{len(eligible)}",
        f"- Holm p<=0.05: {summary['node_level']['holm_p_le_0_05']}/{len(eligible)}",
        f"- Heat-scale robustness: {'PASS' if summary['heat_scale_robustness']['pass'] else 'FAIL'} (max |Δfraction|={max_deviation:.6f})",
        f"- Active-budget saturation: {budget_diagnostics['targets_with_budget_saturation']}/{len(target_runs)} targets",
        "- Zero-diffusion negative control: PASS",
        f"- Source creation-order nonoverlap: {confound['source_creation_order_nonoverlap']}",
        "",
        "## Omnibus controls",
    ]
    for item in omnibus:
        lines.append(f"- {item['label']}: {json.dumps(item, sort_keys=True)}")
    if matched:
        lines.extend(["", "## Degree-matched control", f"- {json.dumps(matched, sort_keys=True)}"])
    (out / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\nBATTERY_COMPLETE")
    print(f"output={out}")
    print(f"node_q95_passes={summary['node_level']['node_q95_passes']}/{len(eligible)}")
    mh_n = summary['node_level']['natural_multihop_targets']
    mh_p = summary['node_level']['natural_multihop_node_q95_passes']
    print(f"natural_multihop_q95_passes={mh_p}/{mh_n}")
    for item in omnibus:
        if item.get("status") == "OK":
            print(
                f"omnibus[{item['label']}]: median_delta={item['real_median_delta']:.6f} "
                f"p={item['median_delta_p_right']:.6g} pass_count={item['real_pass_count']} "
                f"pass_p={item['pass_count_p_right']:.6g}"
            )
    print(f"heat_scale_robustness={'PASS' if summary['heat_scale_robustness']['pass'] else 'FAIL'}")
    print(f"creation_order_confound={confound['source_creation_order_nonoverlap']}")


if __name__ == "__main__":
    main()
