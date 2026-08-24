#!/usr/bin/env python3
"""Replay and randomize document ingestion order to audit a major Rung-0 confound."""
from __future__ import annotations

import argparse
import json
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import random
import statistics

from vll_organism.dynamics import KnowledgeDynamics, cosine_similarity
from vll_organism.graph import KnowledgeGraph

from rung0_common import (
    atomic_json, base_name, clean_template, load_frozen_corpus, real_fraction,
    resolve_source, run_heat, source_class_sets, validate_group_provenance,
    write_csv,
)
from rung0_stats import percentile


def require_single_source_provenance(corpus) -> None:
    multi = [(cid, sorted(srcs)) for cid, srcs in corpus.source_sets.items() if len(srcs) != 1]
    if multi:
        raise RuntimeError(
            "document-order replay is ambiguous when chunks have zero/multiple source provenance; "
            f"first conflicts: {multi[:8]}"
        )


class SimilarityOracle:
    """Bounded LRU cache for candidate-local cosine evaluations.

    Graph replay must follow the runtime's bounded candidate path rather than
    precomputing a dense all-pairs similarity matrix. The cache only memoizes
    candidate pairs actually requested by replay and has a hard entry budget.
    """

    def __init__(self, corpus, max_entries: int = 50000):
        if max_entries < 1:
            raise ValueError("similarity cache size must be >= 1")
        self.corpus = corpus
        self.max_entries = int(max_entries)
        self.cache: OrderedDict[tuple[str, str], float] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.peak_entries = 0

    def __call__(self, left: str, right: str) -> float:
        key = (left, right) if left < right else (right, left)
        cached = self.cache.pop(key, None)
        if cached is not None:
            self.cache[key] = cached
            self.hits += 1
            return cached
        value = cosine_similarity(
            self.corpus.records[left].embedding, self.corpus.records[right].embedding
        )
        self.cache[key] = value
        self.misses += 1
        if len(self.cache) > self.max_entries:
            self.cache.popitem(last=False)
            self.evictions += 1
        self.peak_entries = max(self.peak_entries, len(self.cache))
        return value

    def stats(self) -> dict:
        return {
            "max_entries": self.max_entries,
            "peak_entries": self.peak_entries,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
        }


def replay_graph(
    corpus,
    order: list[str],
    similarity,
    *,
    similarity_threshold: float,
    similarity_candidate_cap: int,
    max_out_degree: int,
) -> KnowledgeGraph:
    dynamics = KnowledgeDynamics(corpus.dynamics_config)
    graph = KnowledgeGraph(max_out_degree_similarity=max_out_degree)
    inserted: list[str] = []
    territory: dict[str, int] = {}
    candidate_cap = similarity_candidate_cap + 1

    for seq, cid in enumerate(order):
        record = corpus.records[cid]
        assignment = dynamics.choose_territory(record.embedding)
        dynamics.register_chunk(cid, float(seq + 1), record.embedding, assignment=assignment, cold=True)
        territory[cid] = assignment.territory
        graph.add_node(cid)

        nearest = [tid for tid, _sim in dynamics.nearest_territories(record.embedding, k=3)]
        candidates: list[str] = []
        if nearest:
            quota = max(1, (candidate_cap + len(nearest) - 1) // len(nearest))
            for tid in nearest:
                recent = [x for x in reversed(inserted + [cid]) if territory.get(x) == tid]
                candidates.extend(recent[:quota])
            candidates = candidates[:candidate_cap]
        else:
            candidates = list(reversed(inserted + [cid]))[:candidate_cap]

        if graph.out_degree(cid) < max_out_degree:
            scored = []
            for other in candidates:
                if other == cid:
                    continue
                value = similarity(cid, other)
                if value >= similarity_threshold:
                    scored.append((value, other))
            scored.sort(reverse=True)
            budget = max_out_degree - graph.out_degree(cid)
            for weight, other in scored[:budget]:
                graph.add_edge(cid, other, "similar_to", weight)
        inserted.append(cid)
    return graph


def structural_edges(graph: KnowledgeGraph) -> set[tuple[str, str, str]]:
    return {(source, edge.target, edge.relation) for source, edge in graph.all_edges()}


def max_weight_error(graph: KnowledgeGraph, actual_rows) -> float:
    replay = {(s, edge.target, edge.relation): edge.weight for s, edge in graph.all_edges()}
    actual = {(s, t, rel): float(w) for s, t, rel, w in actual_rows}
    common = set(replay).intersection(actual)
    return max((abs(replay[k] - actual[k]) for k in common), default=0.0)


def routing_summary(corpus, template, graph, origin_ids, related_ids, heat, ticks) -> dict:
    fractions = []
    hops = []
    top_related = 0
    for target in sorted(origin_ids):
        run = run_heat(template, corpus, target, heat, [ticks], provider=graph.weighted_neighbors)
        auc = run.node_auc_by_horizon[ticks]
        frac, *_ = real_fraction(auc, origin_ids, related_ids)
        if frac is None:
            continue
        fractions.append(frac)
        # local BFS on the replay graph
        seen = {target}
        frontier = [(target, 0)]
        found = None
        while frontier:
            node, depth = frontier.pop(0)
            for nbr, _weight in graph.weighted_neighbors(node, 12):
                if nbr in seen:
                    continue
                if nbr in related_ids:
                    found = depth + 1
                    frontier = []
                    break
                seen.add(nbr)
                frontier.append((nbr, depth + 1))
        if found is not None:
            hops.append(found)
        related_auc = sum(auc[cid] for cid in related_ids)
        foreign_by_source = defaultdict(float)
        for cid, value in auc.items():
            if cid in origin_ids:
                continue
            foreign_by_source[corpus.primary_sources[cid]] += value
        if foreign_by_source:
            top = max(foreign_by_source.items(), key=lambda kv: kv[1])[0]
            if any(cid in related_ids and corpus.primary_sources[cid] == top for cid in related_ids):
                top_related += 1
    return {
        "targets_with_foreign_heat": len(fractions),
        "median_related_fraction": statistics.median(fractions) if fractions else None,
        "mean_related_fraction": sum(fractions) / len(fractions) if fractions else None,
        "hops_1": sum(h == 1 for h in hops),
        "hops_2": sum(h == 2 for h in hops),
        "hops_3_plus": sum(h >= 3 for h in hops),
        "top_destination_related_count": top_related,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="VLL document-ingestion-order sensitivity audit")
    ap.add_argument("--db", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--related", action="append", required=True)
    ap.add_argument("--heat", type=float, default=10.0)
    ap.add_argument("--ticks", type=int, default=120)
    ap.add_argument("--controls", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20260824)
    ap.add_argument("--similarity-threshold", type=float, default=0.55)
    ap.add_argument("--similarity-candidate-cap", type=int, default=200)
    ap.add_argument("--max-out-degree", type=int, default=6)
    ap.add_argument("--similarity-cache-size", type=int, default=50000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    if args.controls < 10:
        raise SystemExit("order sensitivity audit requires at least 10 randomized document orders")
    if args.similarity_cache_size < 1:
        raise SystemExit("--similarity-cache-size must be >= 1")

    out = Path(args.out).resolve()
    if out.exists() and not args.resume:
        raise SystemExit(f"refusing to overwrite output directory: {out}")
    out.mkdir(parents=True, exist_ok=True)

    corpus = load_frozen_corpus(args.db)
    require_single_source_provenance(corpus)
    origin_source = resolve_source(corpus, args.source)
    related_sources = [resolve_source(corpus, value) for value in args.related]
    validate_group_provenance(corpus, origin_source, related_sources)
    origin_ids, related_ids = source_class_sets(corpus, origin_source, related_sources)
    template = clean_template(corpus)

    print("ORDER-SENSITIVITY AUDIT")
    print(f"db={corpus.db_path} nodes={len(corpus.ids)} controls={args.controls}")
    print(f"candidate-local similarity cache entries={args.similarity_cache_size}")
    similarity = SimilarityOracle(corpus, args.similarity_cache_size)

    actual_order = sorted(corpus.ids, key=lambda cid: (float(corpus.records[cid].created_at), cid))
    replay = replay_graph(
        corpus, actual_order, similarity,
        similarity_threshold=args.similarity_threshold,
        similarity_candidate_cap=args.similarity_candidate_cap,
        max_out_degree=args.max_out_degree,
    )
    stored_struct = structural_edges(corpus.graph)
    replay_struct = structural_edges(replay)
    replay_ok = stored_struct == replay_struct
    if not replay_ok:
        missing = list(stored_struct - replay_struct)[:8]
        extra = list(replay_struct - stored_struct)[:8]
        raise RuntimeError(
            "graph replay does not reproduce frozen topology; refusing order randomization. "
            f"missing={missing} extra={extra}. Supply the actual graph-construction config."
        )
    actual_rows = [(s, edge.target, edge.relation, edge.weight) for s, edge in corpus.graph.all_edges()]
    weight_error = max_weight_error(replay, actual_rows)
    if weight_error > 1e-6:
        raise RuntimeError(f"graph replay weight error too large: {weight_error}")

    actual = routing_summary(corpus, template, corpus.graph, origin_ids, related_ids, args.heat, args.ticks)
    sources = sorted(corpus.source_names)
    by_source = {
        source: sorted(
            [cid for cid in corpus.ids if source in corpus.source_sets[cid]],
            key=lambda cid: (float(corpus.records[cid].created_at), cid),
        )
        for source in sources
    }
    parameter_guard = {
        "db": corpus.db_path,
        "source": origin_source,
        "related_sources": related_sources,
        "heat": args.heat,
        "ticks": args.ticks,
        "controls": args.controls,
        "seed": args.seed,
        "similarity_threshold": args.similarity_threshold,
        "similarity_candidate_cap": args.similarity_candidate_cap,
        "max_out_degree": args.max_out_degree,
        "similarity_cache_size": args.similarity_cache_size,
    }
    partial_path = out / "partial_controls.json"
    random_rows = []
    if args.resume and partial_path.is_file():
        partial = json.loads(partial_path.read_text(encoding="utf-8"))
        if partial.get("parameters") != parameter_guard:
            raise RuntimeError("order-sensitivity resume refused: parameters differ from partial checkpoint")
        random_rows = list(partial.get("controls", []))
        if len(random_rows) > args.controls:
            raise RuntimeError("partial checkpoint contains more controls than requested")
        print(f"resuming randomized orders at {len(random_rows)}/{args.controls}")

    rng = random.Random(args.seed)
    for _ in range(len(random_rows)):
        skipped_order = sources[:]
        rng.shuffle(skipped_order)
    stored_edges = structural_edges(corpus.graph)
    for index in range(len(random_rows), args.controls):
        doc_order = sources[:]
        rng.shuffle(doc_order)
        order = [cid for source in doc_order for cid in by_source[source]]
        graph = replay_graph(
            corpus, order, similarity,
            similarity_threshold=args.similarity_threshold,
            similarity_candidate_cap=args.similarity_candidate_cap,
            max_out_degree=args.max_out_degree,
        )
        summary = routing_summary(corpus, template, graph, origin_ids, related_ids, args.heat, args.ticks)
        edge_set = structural_edges(graph)
        union = len(stored_edges | edge_set)
        summary["control"] = index + 1
        summary["edge_jaccard_vs_stored"] = len(stored_edges & edge_set) / union if union else 1.0
        random_rows.append(summary)
        atomic_json(partial_path, {"parameters": parameter_guard, "controls": random_rows})
        print(
            f"[{index + 1:03d}/{args.controls:03d}] median={summary['median_related_fraction']:.4f} "
            f"hops1/2/3+={summary['hops_1']}/{summary['hops_2']}/{summary['hops_3_plus']} "
            f"edgeJ={summary['edge_jaccard_vs_stored']:.3f}"
        )

    medians = [r["median_related_fraction"] for r in random_rows if r["median_related_fraction"] is not None]
    jaccards = [r["edge_jaccard_vs_stored"] for r in random_rows]
    result = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "db": corpus.db_path,
        "source": origin_source,
        "related_sources": related_sources,
        "parameters": {k: v for k, v in parameter_guard.items() if k != "db"},
        "replay_validation": {
            "structural_edge_match": replay_ok,
            "stored_edges": len(stored_struct),
            "replayed_edges": len(replay_struct),
            "max_abs_weight_error": weight_error,
        },
        "stored_order": actual,
        "similarity_cache": similarity.stats(),
        "random_order_distribution": {
            "median_related_fraction_median": statistics.median(medians) if medians else None,
            "median_related_fraction_q05": percentile(medians, 0.05) if medians else None,
            "median_related_fraction_q95": percentile(medians, 0.95) if medians else None,
            "stored_order_percentile": (
                (1 + sum(x <= actual["median_related_fraction"] for x in medians)) / (len(medians) + 1)
                if medians and actual["median_related_fraction"] is not None else None
            ),
            "edge_jaccard_median": statistics.median(jaccards) if jaccards else None,
            "edge_jaccard_q05": percentile(jaccards, 0.05) if jaccards else None,
            "edge_jaccard_q95": percentile(jaccards, 0.95) if jaccards else None,
        },
        "controls": random_rows,
        "interpretation_boundary": (
            "This is a robustness/sensitivity analysis, not a null test of semantic association. "
            "It asks whether routing behavior depends strongly on document arrival order."
        ),
    }
    atomic_json(out / "results.json", result)
    write_csv(out / "controls.csv", random_rows, list(random_rows[0].keys()))
    print("\nORDER_AUDIT_COMPLETE")
    print(f"output={out}")
    print(f"replay_edge_match={replay_ok} max_weight_error={weight_error:.3g}")
    print(f"similarity_cache={similarity.stats()}")
    print(f"stored_median={actual['median_related_fraction']:.6f}")
    print(
        f"random_order_median={result['random_order_distribution']['median_related_fraction_median']:.6f} "
        f"q05={result['random_order_distribution']['median_related_fraction_q05']:.6f} "
        f"q95={result['random_order_distribution']['median_related_fraction_q95']:.6f}"
    )


if __name__ == "__main__":
    main()
