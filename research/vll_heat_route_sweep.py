#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import math
import os
import random
import statistics
from collections import Counter, defaultdict, deque

from vll_organism.dynamics import DynamicsConfig, KnowledgeDynamics
from vll_organism.graph import KnowledgeGraph
from vll_organism.storage import Storage


def base_name(path: str | None) -> str:
    return os.path.basename(path or "(unknown)")


def zero_heat(d: KnowledgeDynamics) -> None:
    d._active_queue.clear()
    d._active_set.clear()
    d._total_heat = 0.0
    for s in d.iter_states():
        s.heat = 0.0
        s.last_decay_tick = d.tick_count


def pulse(d: KnowledgeDynamics, node_id: str, amount: float) -> None:
    s = d.get_state(node_id)
    if s is None:
        raise RuntimeError(f"target chunk missing from dynamics: {node_id}")
    s.heat += amount
    s.last_decay_tick = d.tick_count
    d._total_heat += amount
    d._activate(node_id)


def classify(source: str, target_source: str, related_terms: list[str]) -> str:
    name = base_name(source)
    if name == target_source:
        return "origin"
    low = name.lower()
    if any(term.lower() in low for term in related_terms):
        return "related"
    return "unrelated"


def make_provider(graph: KnowledgeGraph, ids: list[str], target: str, rng: random.Random | None):
    if rng is None:
        return graph.weighted_neighbors

    others = [x for x in ids if x != target]
    shuffled = others[:]
    rng.shuffle(shuffled)

    # Preserve the exact weighted graph and fixed pulse vertex. Only semantic
    # identities of non-target chunks are permuted over graph positions.
    pos = {target: target}
    pos.update(zip(others, shuffled))
    inv = {v: k for k, v in pos.items()}

    def provider(memory_id: str, limit: int = 12):
        graph_pos = pos[memory_id]
        return [
            (inv[nbr], weight)
            for nbr, weight in graph.weighted_neighbors(graph_pos, limit)
            if nbr in inv
        ]

    return provider


def run_trial(
    template: dict,
    graph: KnowledgeGraph,
    ids: list[str],
    sources: dict[str, str],
    target: str,
    target_source: str,
    related_terms: list[str],
    heat: float,
    ticks: int,
    rng: random.Random | None,
    capture_sources: bool = False,
):
    d = KnowledgeDynamics.from_dict(copy.deepcopy(template))
    zero_heat(d)
    pulse(d, target, heat)
    provider = make_provider(graph, ids, target, rng)

    auc = defaultdict(float)
    source_auc = defaultdict(float)
    for _ in range(ticks):
        d.advance(provider)
        group_heat = defaultdict(float)
        for s in d.iter_states():
            src = base_name(sources.get(s.id, ""))
            group_heat[classify(src, target_source, related_terms)] += s.heat
            if capture_sources and src != target_source:
                source_auc[src] += s.heat
        for cls, value in group_heat.items():
            auc[cls] += value

    rel = auc["related"]
    unrel = auc["unrelated"]
    foreign = rel + unrel
    return {
        "related_fraction": rel / foreign if foreign > 0 else 0.0,
        "ratio": rel / unrel if unrel > 0 else math.inf,
        "source_auc": dict(source_auc),
    }


def percentile(values: list[float], p: float) -> float:
    xs = sorted(values)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


def shortest_related_hops(
    graph: KnowledgeGraph,
    target: str,
    id_set: set[str],
    sources: dict[str, str],
    target_source: str,
    related_terms: list[str],
) -> int | None:
    seen = {target}
    q = deque([(target, 0)])
    while q:
        cur, depth = q.popleft()
        for nbr, _ in graph.weighted_neighbors(cur, 12):
            if nbr not in id_set or nbr in seen:
                continue
            if classify(sources.get(nbr, ""), target_source, related_terms) == "related":
                return depth + 1
            seen.add(nbr)
            q.append((nbr, depth + 1))
    return None


def local_profile(
    graph: KnowledgeGraph,
    target: str,
    id_set: set[str],
    sources: dict[str, str],
    target_source: str,
    related_terms: list[str],
):
    counts = Counter()
    weights = defaultdict(float)
    for nbr, weight in graph.weighted_neighbors(target, 12):
        if nbr not in id_set:
            continue
        cls = classify(sources.get(nbr, ""), target_source, related_terms)
        counts[cls] += 1
        weights[cls] += weight
    return counts, weights


def main() -> None:
    ap = argparse.ArgumentParser(
        description="All-target controlled semantic heat-routing sweep"
    )
    ap.add_argument("--db", default="./organism.db")
    ap.add_argument("--source", required=True, help="basename of source document to pulse")
    ap.add_argument("--heat", type=float, default=10.0)
    ap.add_argument("--ticks", type=int, default=120)
    ap.add_argument("--controls", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20260824)
    ap.add_argument("--related", action="append", required=True,
                    help="substring marking a foreign source as related; repeatable")
    ap.add_argument("--target-chunk", default=None,
                    help="optional exact chunk id; otherwise sweep every chunk in --source")
    ap.add_argument("--no-direct-related-only", action="store_true",
                    help="run only source chunks with zero related nodes among their top-12 diffusion neighbors")
    args = ap.parse_args()

    storage = Storage(args.db, read_only=True, initialize=False)
    try:
        records = storage.all_chunks()
        snapshot = storage.load_dynamics_snapshot()
        edges = storage.all_edges()
    finally:
        storage.close()

    if snapshot is not None and int(snapshot.get("version", 0)) == KnowledgeDynamics.PERSISTENCE_VERSION:
        config = DynamicsConfig(**dict(snapshot.get("config", {})))
        config_source = f"snapshot tick {snapshot.get('tick')}"
    else:
        config = DynamicsConfig()
        config_source = "current code defaults (no compatible snapshot found)"

    usable = [r for r in records if r.embedding is not None]
    ids = [r.id for r in usable]
    id_set = set(ids)
    sources = {r.id: (r.source or "") for r in usable}
    rec_by_id = {r.id: r for r in usable}

    clean = KnowledgeDynamics(config)
    for r in sorted(usable, key=lambda x: (x.created_at, x.id)):
        clean.register_chunk(r.id, r.created_at, r.embedding, cold=True)
    zero_heat(clean)
    template = clean.to_dict()

    graph = KnowledgeGraph()
    graph.load_edge_list(edges)
    for cid in ids:
        graph.add_node(cid)

    targets = [r.id for r in usable if base_name(r.source) == args.source]
    if args.target_chunk is not None:
        targets = [cid for cid in targets if cid == args.target_chunk]
    if not targets:
        available = sorted({base_name(r.source) for r in usable})
        detail = f" and target {args.target_chunk!r}" if args.target_chunk else ""
        raise SystemExit(f"No chunks found for {args.source!r}{detail}. Available sources: {available}")

    rows = []
    master = random.Random(args.seed)

    print("ALL-TARGET HEAT-ROUTING SWEEP")
    print(f"db: {os.path.abspath(args.db)}")
    print(f"dynamics_config: {config_source}")
    print(f"durable_nodes: {len(ids)}  stored_edges: {len(edges)}")
    print(f"source: {args.source}  source_chunks: {len(targets)}")
    print(f"related_terms: {args.related}")
    print(f"pulse_heat: {args.heat}  ticks: {args.ticks}  controls_per_target: {args.controls}")
    print("control: exact weighted graph preserved; non-target semantic identities permuted")
    if args.no_direct_related_only:
        print("filter: only natural multi-hop starts (zero direct related diffusion neighbors)")
    print()

    for idx, target in enumerate(sorted(targets), 1):
        target_source = base_name(sources[target])
        counts, weights = local_profile(
            graph, target, id_set, sources, target_source, args.related
        )
        min_hops = shortest_related_hops(
            graph, target, id_set, sources, target_source, args.related
        )
        if args.no_direct_related_only and counts["related"] != 0:
            continue

        real = run_trial(
            template, graph, ids, sources, target, target_source,
            args.related, args.heat, args.ticks, None, True,
        )

        controls = []
        for _ in range(args.controls):
            rng = random.Random(master.randrange(2**63))
            controls.append(
                run_trial(
                    template, graph, ids, sources, target, target_source,
                    args.related, args.heat, args.ticks, rng, False,
                )["related_fraction"]
            )

        med = statistics.median(controls)
        q95 = percentile(controls, 0.95)
        p_emp = (1 + sum(x >= real["related_fraction"] for x in controls)) / (args.controls + 1)
        effect = real["related_fraction"] / med if med > 0 else math.inf
        preview = " ".join(rec_by_id[target].text.split())[:72]
        source_auc = real["source_auc"]
        top_foreign = max(source_auc.items(), key=lambda kv: kv[1])[0] if source_auc else "(none)"
        rows.append({
            "target": target,
            "direct_rel": counts["related"],
            "direct_unrel": counts["unrelated"],
            "direct_origin": counts["origin"],
            "rel_weight": weights["related"],
            "unrel_weight": weights["unrelated"],
            "min_hops": min_hops,
            "real": real["related_fraction"],
            "median": med,
            "q95": q95,
            "effect": effect,
            "p": p_emp,
            "preview": preview,
            "top_foreign": top_foreign,
        })
        print(
            f"[{idx:02d}/{len(targets):02d}] {target} "
            f"hops={min_hops if min_hops is not None else 'NA'} "
            f"nbr(rel/unrel/origin)={counts['related']}/{counts['unrelated']}/{counts['origin']} "
            f"real={real['related_fraction']:.4f} null={med:.4f} "
            f"effect={effect:.2f}x p={p_emp:.4f} top={top_foreign}"
        )

    if not rows:
        print("NO ELIGIBLE TARGETS")
        return

    effects = [r["effect"] for r in rows if math.isfinite(r["effect"])]
    reals = [r["real"] for r in rows]
    passes = [r for r in rows if r["real"] > r["q95"]]
    anti = [r for r in rows if r["real"] < r["median"]]
    natural_multihop = [r for r in rows if r["direct_rel"] == 0 and r["min_hops"] is not None]
    multihop_pass = [r for r in natural_multihop if r["real"] > r["q95"]]

    print("\nSUMMARY")
    print(f"targets_run: {len(rows)}")
    print(f"real_fraction_median: {statistics.median(reals):.6f}")
    print(f"effect_median: {statistics.median(effects):.3f}x")
    print(f"targets_above_95pct_null: {len(passes)}/{len(rows)}")
    print(f"targets_below_null_median: {len(anti)}/{len(rows)}")
    print(f"natural_multihop_targets: {len(natural_multihop)}")
    print(f"natural_multihop_above_95pct_null: {len(multihop_pass)}/{len(natural_multihop) if natural_multihop else 0}")

    print("\nTARGET TABLE")
    print("target\thops\tdirect_rel\tdirect_unrel\treal\tnull_median\tq95\teffect\tp\ttop_foreign")
    for r in sorted(rows, key=lambda x: x["effect"], reverse=True):
        hops = r["min_hops"] if r["min_hops"] is not None else "NA"
        print(
            f"{r['target']}\t{hops}\t{r['direct_rel']}\t{r['direct_unrel']}\t"
            f"{r['real']:.6f}\t{r['median']:.6f}\t{r['q95']:.6f}\t"
            f"{r['effect']:.3f}\t{r['p']:.6f}\t{r['top_foreign']}"
        )

    print("\nNATURAL MULTI-HOP TARGETS")
    if not natural_multihop:
        print("none")
    else:
        for r in sorted(natural_multihop, key=lambda x: x["effect"], reverse=True):
            verdict = "PASS" if r["real"] > r["q95"] else "NO_PASS"
            print(
                f"{verdict} {r['target']} hops={r['min_hops']} "
                f"real={r['real']:.6f} null={r['median']:.6f} "
                f"effect={r['effect']:.3f}x p={r['p']:.6f} :: {r['preview']}"
            )


if __name__ == "__main__":
    main()
