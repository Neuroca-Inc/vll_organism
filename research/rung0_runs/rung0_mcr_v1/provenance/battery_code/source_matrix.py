#!/usr/bin/env python3
"""Source-to-source routing matrix with checkpointed source-fixed permutation controls."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics

from rung0_common import (
    atomic_json, base_name, clean_template, ids_for_source, load_frozen_corpus,
    resolve_source, run_heat, write_csv,
)
from rung0_stats import benjamini_hochberg, empirical_p_right, percentile


def stable_seed(seed: int, text: str) -> int:
    digest = hashlib.sha256(f"{seed}:{text}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def validate_selected_provenance(corpus, sources) -> None:
    selected = set(sources)
    conflicts = []
    for cid, provenance in corpus.source_sets.items():
        hit = selected.intersection(provenance)
        if len(hit) > 1:
            conflicts.append((cid, sorted(hit)))
    if conflicts:
        raise RuntimeError(f"selected source classes overlap through deduplicated chunks: {conflicts[:8]}")


def target_destination_shares(node_auc, position_to_identity, origin_ids, source_ids):
    """Reference implementation used by regression tests and fidelity checks."""
    totals = {source: 0.0 for source in source_ids}
    foreign_total = 0.0
    for pos, auc in node_auc.items():
        identity = position_to_identity[pos]
        if identity in origin_ids:
            continue
        foreign_total += auc
        for source, ids in source_ids.items():
            if identity in ids:
                totals[source] += auc
                break
    if foreign_total <= 0:
        return {source: None for source in source_ids}
    return {source: value / foreign_total for source, value in totals.items()}


def source_checkpoint_name(source: str) -> str:
    digest = hashlib.sha256(source.encode()).hexdigest()[:12]
    return f"source_{digest}.json"


def process_source(corpus, template, source, sources, source_ids, heat, ticks, controls, seed) -> dict:
    origin_ids = source_ids[source]
    destinations = [dest for dest in sources if dest != source]
    foreign_positions = [cid for cid in corpus.ids if cid not in origin_ids]
    foreign_identities = foreign_positions[:]

    label_for_identity = {}
    for identity in foreign_identities:
        label = None
        for index, dest in enumerate(destinations):
            if identity in source_ids[dest]:
                label = index
                break
        label_for_identity[identity] = label

    target_vectors = []
    real_values = [[] for _ in destinations]
    residuals = []
    for target in sorted(origin_ids):
        run = run_heat(template, corpus, target, heat, [ticks])
        auc = run.node_auc_by_horizon[ticks]
        values = [auc[pos] for pos in foreign_positions]
        denom = sum(values)
        if denom <= 0.0:
            continue
        target_vectors.append((target, values, denom))
        totals = [0.0] * len(destinations)
        for value, identity in zip(values, foreign_identities):
            label = label_for_identity[identity]
            if label is not None:
                totals[label] += value
        shares = [value / denom for value in totals]
        for index, share in enumerate(shares):
            real_values[index].append(share)
        residuals.append(max(0.0, 1.0 - sum(shares)))

    if not target_vectors:
        raise RuntimeError(f"source has no targets with measurable foreign heat: {source}")

    rng = random.Random(stable_seed(seed, source))
    control_values = [[] for _ in destinations]
    for _ in range(controls):
        shuffled = foreign_identities[:]
        rng.shuffle(shuffled)
        labels = [label_for_identity[identity] for identity in shuffled]
        sweep_values = [[] for _ in destinations]
        for _target, values, denom in target_vectors:
            totals = [0.0] * len(destinations)
            for value, label in zip(values, labels):
                if label is not None:
                    totals[label] += value
            for index, total in enumerate(totals):
                sweep_values[index].append(total / denom)
        for index, values in enumerate(sweep_values):
            control_values[index].append(statistics.median(values))

    cells = []
    real_row = {}
    for index, dest in enumerate(destinations):
        real = statistics.median(real_values[index]) if real_values[index] else None
        null = control_values[index]
        med = statistics.median(null) if null else None
        real_row[dest] = real
        cells.append({
            "source": source,
            "source_basename": base_name(source),
            "destination": dest,
            "destination_basename": base_name(dest),
            "real_median_foreign_share": real,
            "null_median": med,
            "null_q95": percentile(null, 0.95) if null else None,
            "delta": None if real is None or med is None else real - med,
            "p_right": None if real is None or not null else empirical_p_right(real, null),
        })
    return {
        "source": source,
        "source_basename": base_name(source),
        "targets_with_foreign_heat": len(target_vectors),
        "real_row": real_row,
        "other_foreign_real_median": statistics.median(residuals) if residuals else None,
        "cells": cells,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Frozen VLL source routing matrix")
    ap.add_argument("--db", required=True)
    ap.add_argument("--source", action="append", required=True, help="exact path or unique basename; repeatable")
    ap.add_argument("--heat", type=float, default=10.0)
    ap.add_argument("--ticks", type=int, default=120)
    ap.add_argument("--controls", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=20260824)
    ap.add_argument("--out", required=True)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    if args.controls < 1000:
        raise SystemExit("matrix requires at least 1000 controls")
    if args.heat <= 0 or args.ticks < 1:
        raise SystemExit("heat and ticks must be positive")

    out = Path(args.out).resolve()
    if out.exists() and not args.resume:
        raise SystemExit(f"refusing to overwrite output directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    checkpoints = out / "checkpoints"
    checkpoints.mkdir(exist_ok=True)

    corpus = load_frozen_corpus(args.db)
    sources = [resolve_source(corpus, x) for x in args.source]
    if len(set(sources)) != len(sources) or len(sources) < 2:
        raise SystemExit("matrix needs at least two distinct sources")
    validate_selected_provenance(corpus, sources)
    source_ids = {source: ids_for_source(corpus, source) for source in sources}
    missing = [source for source, ids in source_ids.items() if not ids]
    if missing:
        raise SystemExit(f"selected sources contain no chunks: {missing}")

    template = clean_template(corpus)
    source_results = []
    print("RUNG-0 SOURCE ROUTING MATRIX")
    print(f"db={corpus.db_path} tick={corpus.snapshot_tick} controls={args.controls}")
    for index, source in enumerate(sources, 1):
        checkpoint = checkpoints / source_checkpoint_name(source)
        if args.resume and checkpoint.is_file():
            result = json.loads(checkpoint.read_text(encoding="utf-8"))
            print(f"[{index:02d}/{len(sources):02d}] RESUME {base_name(source)}")
        else:
            print(f"[{index:02d}/{len(sources):02d}] RUN {base_name(source)} chunks={len(source_ids[source])}")
            result = process_source(
                corpus, template, source, sources, source_ids,
                args.heat, args.ticks, args.controls, args.seed,
            )
            atomic_json(checkpoint, result)
        source_results.append(result)

    cells = [cell for result in source_results for cell in result["cells"]]
    qvals = benjamini_hochberg([cell["p_right"] for cell in cells])
    for cell, q in zip(cells, qvals):
        cell["q_bh_right"] = q

    by_source = {result["source"]: result for result in source_results}
    residual_col = "OTHER_FOREIGN"
    header = ["source"] + [base_name(x) for x in sources] + [residual_col]
    matrix_rows = []
    for source in sources:
        result = by_source[source]
        row = {"source": base_name(source)}
        for dest in sources:
            row[base_name(dest)] = "SELF" if source == dest else result["real_row"].get(dest)
        row[residual_col] = result["other_foreign_real_median"]
        matrix_rows.append(row)

    result = {
        "schema_version": 2,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "db": corpus.db_path,
        "snapshot_tick": corpus.snapshot_tick,
        "parameters": {"heat": args.heat, "ticks": args.ticks, "controls": args.controls, "seed": args.seed},
        "sources": sources,
        "source_results": source_results,
        "cells": cells,
    }
    atomic_json(out / "results.json", result)
    write_csv(out / "matrix.csv", matrix_rows, header)
    write_csv(out / "cells.csv", cells, [
        "source_basename", "destination_basename", "real_median_foreign_share", "null_median",
        "null_q95", "delta", "p_right", "q_bh_right",
    ])

    print("\nMATRIX_COMPLETE")
    print(f"output={out}")
    for source in sources:
        value = by_source[source]["other_foreign_real_median"]
        print(f"residual[{base_name(source)}]={value:.4f}" if value is not None else f"residual[{base_name(source)}]=NA")
    for cell in sorted(cells, key=lambda x: (x["q_bh_right"] if x["q_bh_right"] is not None else 2.0, -(x["delta"] or 0.0))):
        print(
            f"{cell['source_basename']} -> {cell['destination_basename']}: "
            f"real={cell['real_median_foreign_share']:.4f} null={cell['null_median']:.4f} "
            f"delta={cell['delta']:.4f} p={cell['p_right']:.5g} q={cell['q_bh_right']:.5g}"
        )


if __name__ == "__main__":
    main()
