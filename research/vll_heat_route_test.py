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

    # Exact weighted topology stays fixed. Only chunk identities are permuted
    # over graph positions. Holding target fixed makes every trial start from
    # the same graph vertex, degree, and incident edge weights.
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


def run_trial(template: dict, graph: KnowledgeGraph, ids: list[str], sources: dict[str, str],
              target: str, target_source: str, related_terms: list[str], heat: float,
              ticks: int, rng: random.Random | None):
    d = KnowledgeDynamics.from_dict(copy.deepcopy(template))
    zero_heat(d)
    pulse(d, target, heat)
    provider = make_provider(graph, ids, target, rng)

    auc = defaultdict(float)
    checkpoints = {}
    checkpoint_ticks = {1, 2, 5, 10, 20, 40, 60, 90, ticks}

    for t in range(1, ticks + 1):
        d.advance(provider)
        group_heat = defaultdict(float)
        for s in d.iter_states():
            cls = classify(sources.get(s.id, ""), target_source, related_terms)
            group_heat[cls] += s.heat
        for cls, value in group_heat.items():
            auc[cls] += value
        if t in checkpoint_ticks:
            checkpoints[t] = dict(group_heat)

    rel = auc["related"]
    unrel = auc["unrelated"]
    foreign = rel + unrel
    ratio = rel / unrel if unrel > 0 else math.inf
    fraction = rel / foreign if foreign > 0 else 0.0
    return {
        "related_auc": rel,
        "unrelated_auc": unrel,
        "ratio": ratio,
        "related_fraction": fraction,
        "origin_auc": auc["origin"],
        "checkpoints": checkpoints,
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


def connected_component(graph: KnowledgeGraph, target: str, valid_ids: set[str]) -> set[str]:
    seen = {target}
    q = deque([target])
    while q:
        cur = q.popleft()
        for nbr, _weight in graph.weighted_neighbors(cur, 12):
            if nbr in valid_ids and nbr not in seen:
                seen.add(nbr)
                q.append(nbr)
    return seen


def main() -> None:
    ap = argparse.ArgumentParser(description="Controlled semantic heat-routing test")
    ap.add_argument("--db", default="./organism.db")
    ap.add_argument("--source", default="00_MCR_Origin.md",
                    help="basename of source document to pulse")
    ap.add_argument("--heat", type=float, default=10.0)
    ap.add_argument("--ticks", type=int, default=120)
    ap.add_argument("--controls", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20260824)
    ap.add_argument("--related", action="append", default=[],
                    help="substring marking a foreign source as semantically related; repeatable")
    args = ap.parse_args()

    related_terms = args.related or ["MCR", "Analogistical-Constructivism", "Inside-the-AI-Model"]

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

    # Build a clean zero-history dynamics template over every durable chunk
    # visible in this read-only DB view. Territory reconstruction cannot affect
    # heat propagation; advance() only consumes node heat + graph neighbors.
    clean = KnowledgeDynamics(config)
    for r in sorted(usable, key=lambda x: (x.created_at, x.id)):
        clean.register_chunk(r.id, r.created_at, r.embedding, cold=True)
    zero_heat(clean)
    template = clean.to_dict()

    graph = KnowledgeGraph()
    graph.load_edge_list(edges)
    for cid in ids:
        graph.add_node(cid)

    target_candidates = [r.id for r in usable if base_name(r.source) == args.source]
    if not target_candidates:
        available = sorted({base_name(r.source) for r in usable})
        raise SystemExit(f"No chunks found for {args.source!r}. Available sources: {available}")

    def bridge_score(cid: str):
        own = base_name(sources[cid])
        foreign_weight = sum(
            w for nbr, w in graph.weighted_neighbors(cid, 12)
            if nbr in id_set and base_name(sources.get(nbr)) != own
        )
        total_weight = sum(w for nbr, w in graph.weighted_neighbors(cid, 12) if nbr in id_set)
        return (foreign_weight, total_weight, cid)

    target = max(target_candidates, key=bridge_score)
    target_record = next(r for r in usable if r.id == target)
    target_source = base_name(target_record.source)

    component = connected_component(graph, target, id_set)
    component_classes = Counter(
        classify(sources.get(cid, ""), target_source, related_terms) for cid in component
    )
    component_sources = Counter(base_name(sources.get(cid)) for cid in component)

    print("CONTROLLED HEAT-ROUTING EXPERIMENT")
    print(f"db: {os.path.abspath(args.db)}")
    print(f"dynamics_config: {config_source}")
    print(f"durable_nodes: {len(ids)}  stored_edges: {len(edges)}")
    print(f"target_source: {target_source}")
    print(f"target_chunk: {target}")
    print(f"target_bridge_score: foreign_weight={bridge_score(target)[0]:.6f} total_weight={bridge_score(target)[1]:.6f}")
    print(f"target_preview: {' '.join(target_record.text.split())[:220]}")
    print(f"pulse_heat: {args.heat}  ticks: {args.ticks}  shuffled_controls: {args.controls}")
    print(f"related_terms: {related_terms}")
    print("control: exact weighted graph is preserved; non-target chunk identities are randomly permuted")
    print()

    print("TARGET CONNECTED COMPONENT")
    print(f"nodes: {len(component)}  origin={component_classes['origin']} related={component_classes['related']} unrelated={component_classes['unrelated']}")
    for src, count in component_sources.most_common():
        print(f"  {count:3d}  {src}")
    if component_classes["unrelated"] == 0:
        print("NOTE: no unrelated node is reachable from the target in the real graph.")
        print("      This run tests semantic/component separation, not finer within-component route preference.")
    else:
        print("NOTE: unrelated nodes are reachable, so this run also tests within-component route preference.")
    print()

    real = run_trial(template, graph, ids, sources, target, target_source,
                     related_terms, args.heat, args.ticks, None)

    controls = []
    master = random.Random(args.seed)
    for _ in range(args.controls):
        controls.append(run_trial(template, graph, ids, sources, target, target_source,
                                  related_terms, args.heat, args.ticks,
                                  random.Random(master.randrange(2**63))))

    c_frac = [x["related_fraction"] for x in controls]
    c_ratio = [x["ratio"] for x in controls if math.isfinite(x["ratio"])]
    p_emp = (1 + sum(x >= real["related_fraction"] for x in c_frac)) / (args.controls + 1)
    med = statistics.median(c_frac)
    q95 = percentile(c_frac, 0.95)
    effect = real["related_fraction"] / med if med > 0 else math.inf

    print("RESULT")
    print(f"REAL related_heat_fraction_AUC = {real['related_fraction']:.6f}")
    print(f"REAL related/unrelated_AUC_ratio = {real['ratio']:.6f}")
    print(f"CONTROL median related_fraction = {med:.6f}")
    print(f"CONTROL 95th percentile       = {q95:.6f}")
    print(f"REAL/control_median effect    = {effect:.3f}x")
    print(f"empirical p (one-sided)       = {p_emp:.6f}")
    if c_ratio:
        print(f"CONTROL median ratio          = {statistics.median(c_ratio):.6f}")
    print()

    print("REAL ROUTING CHECKPOINTS")
    print("tick\torigin\trelated\tunrelated\trelated_fraction_foreign")
    for t in sorted(real["checkpoints"]):
        g = real["checkpoints"][t]
        rel = g.get("related", 0.0)
        unr = g.get("unrelated", 0.0)
        frac = rel / (rel + unr) if rel + unr > 0 else 0.0
        print(f"{t}\t{g.get('origin',0.0):.6f}\t{rel:.6f}\t{unr:.6f}\t{frac:.6f}")

    print()
    if real["related_fraction"] > q95:
        print("PASS: real semantic/source alignment exceeded the 95th percentile of identity-shuffled controls.")
    else:
        print("NO PASS: real routing did not exceed the 95th percentile of identity-shuffled controls.")


if __name__ == "__main__":
    main()
