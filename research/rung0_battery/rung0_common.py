#!/usr/bin/env python3
"""Shared helpers for frozen VLL Rung-0 routing experiments."""
from __future__ import annotations

from dataclasses import dataclass, replace
import copy
import csv
import hashlib
import json
import math
import os
from pathlib import Path
from collections import Counter, defaultdict, deque
from typing import Callable, Iterable, Sequence

from vll_organism.dynamics import DynamicsConfig, KnowledgeDynamics
from vll_organism.graph import KnowledgeGraph
from vll_organism.storage import Storage


@dataclass(frozen=True)
class FrozenCorpus:
    db_path: str
    records: dict[str, object]
    ids: tuple[str, ...]
    graph: KnowledgeGraph
    dynamics_config: DynamicsConfig
    snapshot_tick: int | None
    source_sets: dict[str, frozenset[str]]
    primary_sources: dict[str, str]
    source_names: tuple[str, ...]
    meta: dict[str, str]


@dataclass(frozen=True)
class HeatRun:
    node_auc_by_horizon: dict[int, dict[str, float]]
    source_auc_by_horizon: dict[int, dict[str, float]]
    checkpoints: dict[int, dict[str, float]]
    max_active_nodes: int
    budget_saturation_ticks: int


def base_name(path: str | None) -> str:
    return os.path.basename(path or "(unknown)")


def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path: str | os.PathLike[str], data: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def write_csv(path: str | os.PathLike[str], rows: Sequence[dict], fields: Sequence[str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_frozen_corpus(db_path: str) -> FrozenCorpus:
    storage = Storage(db_path, read_only=True, initialize=False)
    try:
        records_list = storage.all_chunks()
        snapshot = storage.load_dynamics_snapshot()
        edges = storage.all_edges()
        meta_rows = {}
        for key in (
            "embedding_dim", "embedding_model", "tick_interval_s", "daemon_state",
            "watch_folder_path", "watch_folder_ok",
        ):
            value = storage.get_meta(key)
            if value is not None:
                meta_rows[key] = value
        source_sets = {
            record.id: frozenset(storage.get_chunk_sources(record.id) or (() if not record.source else (record.source,)))
            for record in records_list
        }
    finally:
        storage.close()

    usable = [r for r in records_list if r.embedding is not None]
    if not usable:
        raise RuntimeError("snapshot contains no embedded chunks")
    ids = tuple(r.id for r in usable)
    id_set = set(ids)
    records = {r.id: r for r in usable}

    dims = {len(r.embedding) for r in usable if r.embedding is not None}
    if len(dims) != 1:
        raise RuntimeError(f"inconsistent embedding dimensions in snapshot: {sorted(dims)}")
    recorded_dim = meta_rows.get("embedding_dim")
    if recorded_dim is not None and int(recorded_dim) != next(iter(dims)):
        raise RuntimeError(
            f"embedding metadata mismatch: meta={recorded_dim}, actual={next(iter(dims))}"
        )

    bad_edges = [(s, t) for s, t, _rel, _w in edges if s not in id_set or t not in id_set]
    if bad_edges:
        preview = bad_edges[:5]
        raise RuntimeError(f"graph contains {len(bad_edges)} edge endpoint(s) without usable chunks: {preview}")

    graph = KnowledgeGraph()
    graph.load_edge_list(edges)
    for cid in ids:
        graph.add_node(cid)

    if snapshot is not None and int(snapshot.get("version", 0)) == KnowledgeDynamics.PERSISTENCE_VERSION:
        config = DynamicsConfig(**dict(snapshot.get("config", {})))
        snapshot_tick = int(snapshot.get("tick", 0))
    else:
        raise RuntimeError(
            "frozen DB has no compatible dynamics snapshot; refusing to substitute current defaults"
        )

    primary_sources = {r.id: (r.source or "") for r in usable}
    all_sources = sorted({src for sources in source_sets.values() for src in sources if src})
    return FrozenCorpus(
        db_path=os.path.abspath(db_path),
        records=records,
        ids=ids,
        graph=graph,
        dynamics_config=config,
        snapshot_tick=snapshot_tick,
        source_sets=source_sets,
        primary_sources=primary_sources,
        source_names=tuple(all_sources),
        meta=meta_rows,
    )


def resolve_source(corpus: FrozenCorpus, selector: str) -> str:
    if selector in corpus.source_names:
        return selector
    matches = [src for src in corpus.source_names if base_name(src) == selector]
    if not matches:
        available = sorted(base_name(src) for src in corpus.source_names)
        raise ValueError(f"source {selector!r} not found; available basenames: {available}")
    if len(matches) > 1:
        raise ValueError(
            f"source basename {selector!r} is ambiguous; use one exact path: {matches}"
        )
    return matches[0]


def ids_for_source(corpus: FrozenCorpus, source: str) -> set[str]:
    return {cid for cid, sources in corpus.source_sets.items() if source in sources}


def validate_group_provenance(
    corpus: FrozenCorpus,
    origin_source: str,
    related_sources: Sequence[str],
) -> None:
    relevant = {origin_source, *related_sources}
    conflicts = []
    for cid, sources in corpus.source_sets.items():
        hit = relevant.intersection(sources)
        if len(hit) > 1:
            conflicts.append((cid, sorted(hit)))
    if conflicts:
        raise RuntimeError(
            "deduplicated chunks cross experimental source classes; classification would be ambiguous: "
            f"{conflicts[:8]}"
        )


def clean_template(corpus: FrozenCorpus, *, config: DynamicsConfig | None = None) -> dict:
    dynamics = KnowledgeDynamics(config or corpus.dynamics_config)
    for cid in sorted(
        corpus.ids,
        key=lambda x: (float(corpus.records[x].created_at), x),
    ):
        record = corpus.records[cid]
        dynamics.register_chunk(cid, record.created_at, record.embedding, cold=True)
    zero_heat(dynamics)
    return dynamics.to_dict()


def zero_heat(dynamics: KnowledgeDynamics) -> None:
    dynamics._active_queue.clear()
    dynamics._active_set.clear()
    dynamics._total_heat = 0.0
    for state in dynamics.iter_states():
        state.heat = 0.0
        state.last_decay_tick = dynamics.tick_count


def pulse(dynamics: KnowledgeDynamics, node_id: str, amount: float) -> None:
    if amount <= 0:
        raise ValueError("pulse heat must be > 0")
    state = dynamics.get_state(node_id)
    if state is None:
        raise RuntimeError(f"target chunk missing from dynamics: {node_id}")
    state.heat += float(amount)
    state.last_decay_tick = dynamics.tick_count
    dynamics._total_heat += float(amount)
    dynamics._activate(node_id)


def run_heat(
    template: dict,
    corpus: FrozenCorpus,
    target: str,
    heat: float,
    horizons: Sequence[int],
    *,
    provider: Callable[[str, int], Iterable[tuple[str, float]]] | None = None,
    checkpoint_ticks: Sequence[int] = (),
) -> HeatRun:
    horizons = sorted(set(int(h) for h in horizons))
    if not horizons or horizons[0] < 1:
        raise ValueError("horizons must contain positive tick counts")
    dynamics = KnowledgeDynamics.from_dict(copy.deepcopy(template))
    zero_heat(dynamics)
    pulse(dynamics, target, heat)
    neighbor_provider = provider or corpus.graph.weighted_neighbors

    node_auc = {cid: 0.0 for cid in corpus.ids}
    source_auc = defaultdict(float)
    node_by_horizon: dict[int, dict[str, float]] = {}
    source_by_horizon: dict[int, dict[str, float]] = {}
    checkpoints: dict[int, dict[str, float]] = {}
    checkpoint_set = set(int(x) for x in checkpoint_ticks)
    max_active_nodes = dynamics.active_heat()[1]
    budget_saturation_ticks = 0

    for tick in range(1, horizons[-1] + 1):
        active_before = dynamics.active_heat()[1]
        if active_before > dynamics.config.active_budget:
            budget_saturation_ticks += 1
        dynamics.advance(neighbor_provider)
        max_active_nodes = max(max_active_nodes, dynamics.active_heat()[1])
        source_heat = defaultdict(float)
        for state in dynamics.iter_states():
            h = float(state.heat)
            node_auc[state.id] += h
            src = corpus.primary_sources.get(state.id, "")
            source_auc[src] += h
            source_heat[src] += h
        if tick in checkpoint_set:
            checkpoints[tick] = dict(source_heat)
        if tick in horizons:
            node_by_horizon[tick] = dict(node_auc)
            source_by_horizon[tick] = dict(source_auc)

    return HeatRun(
        node_by_horizon, source_by_horizon, checkpoints,
        max_active_nodes=max_active_nodes,
        budget_saturation_ticks=budget_saturation_ticks,
    )


def source_class_sets(
    corpus: FrozenCorpus,
    origin_source: str,
    related_sources: Sequence[str],
) -> tuple[set[str], set[str]]:
    origin_ids = ids_for_source(corpus, origin_source)
    related_ids: set[str] = set()
    for source in related_sources:
        related_ids.update(ids_for_source(corpus, source))
    related_ids.difference_update(origin_ids)
    return origin_ids, related_ids


def fraction_from_assignment(
    node_auc: dict[str, float],
    position_to_identity: dict[str, str],
    origin_ids: set[str],
    related_ids: set[str],
) -> tuple[float | None, float, float, float]:
    origin = 0.0
    related = 0.0
    other_foreign = 0.0
    for position, auc in node_auc.items():
        identity = position_to_identity[position]
        if identity in origin_ids:
            origin += auc
        elif identity in related_ids:
            related += auc
        else:
            other_foreign += auc
    foreign = related + other_foreign
    fraction = None if foreign <= 0.0 else related / foreign
    return fraction, origin, related, other_foreign


def real_fraction(
    node_auc: dict[str, float], origin_ids: set[str], related_ids: set[str]
) -> tuple[float | None, float, float, float]:
    assignment = {cid: cid for cid in node_auc}
    return fraction_from_assignment(node_auc, assignment, origin_ids, related_ids)


def source_auc_real(
    corpus: FrozenCorpus,
    node_auc: dict[str, float],
    origin_source: str,
) -> dict[str, float]:
    out = defaultdict(float)
    for cid, auc in node_auc.items():
        src = corpus.primary_sources.get(cid, "")
        if src != origin_source:
            out[src] += auc
    return dict(out)


def neighbor_profile(
    corpus: FrozenCorpus,
    target: str,
    origin_ids: set[str],
    related_ids: set[str],
) -> dict[str, float | int]:
    counts = Counter()
    weights = defaultdict(float)
    for nbr, weight in corpus.graph.weighted_neighbors(target, 12):
        if nbr in origin_ids:
            cls = "origin"
        elif nbr in related_ids:
            cls = "related"
        else:
            cls = "other_foreign"
        counts[cls] += 1
        weights[cls] += float(weight)
    return {
        "direct_origin": counts["origin"],
        "direct_related": counts["related"],
        "direct_other_foreign": counts["other_foreign"],
        "direct_origin_weight": weights["origin"],
        "direct_related_weight": weights["related"],
        "direct_other_foreign_weight": weights["other_foreign"],
    }


def shortest_hops(
    corpus: FrozenCorpus,
    target: str,
    destination_ids: set[str],
    *,
    provider: Callable[[str, int], Iterable[tuple[str, float]]] | None = None,
) -> int | None:
    if target in destination_ids:
        return 0
    neighbors = provider or corpus.graph.weighted_neighbors
    seen = {target}
    q = deque([(target, 0)])
    while q:
        node, depth = q.popleft()
        for nbr, _weight in neighbors(node, 12):
            if nbr in seen or nbr not in corpus.records:
                continue
            if nbr in destination_ids:
                return depth + 1
            seen.add(nbr)
            q.append((nbr, depth + 1))
    return None


def flat_weight_provider(corpus: FrozenCorpus):
    def provider(memory_id: str, limit: int = 12):
        return [(nbr, 1.0) for nbr, _weight in corpus.graph.weighted_neighbors(memory_id, limit)]
    return provider


def lesion_target_related_provider(corpus: FrozenCorpus, target: str, related_ids: set[str]):
    def provider(memory_id: str, limit: int = 12):
        rows = corpus.graph.weighted_neighbors(memory_id, limit)
        if memory_id != target:
            return rows
        return [(nbr, w) for nbr, w in rows if nbr not in related_ids]
    return provider


def with_diffusion(config: DynamicsConfig, value: float) -> DynamicsConfig:
    return replace(config, diffusion_fraction=float(value))


def graph_degree(corpus: FrozenCorpus, node_id: str) -> int:
    return len(corpus.graph.weighted_neighbors(node_id, 12))


def graph_weighted_degree(corpus: FrozenCorpus, node_id: str) -> float:
    return sum(float(w) for _nbr, w in corpus.graph.weighted_neighbors(node_id, 12))


def source_inventory(corpus: FrozenCorpus) -> list[dict]:
    by_source: dict[str, list[str]] = defaultdict(list)
    for cid, sources in corpus.source_sets.items():
        for source in sources:
            by_source[source].append(cid)
    created = sorted((float(corpus.records[cid].created_at), cid) for cid in corpus.ids)
    rank = {cid: i for i, (_ts, cid) in enumerate(created)}
    rows = []
    for source, ids in sorted(by_source.items(), key=lambda kv: base_name(kv[0])):
        degrees = [graph_degree(corpus, cid) for cid in ids]
        weighted = [graph_weighted_degree(corpus, cid) for cid in ids]
        ranks = [rank[cid] for cid in ids]
        rows.append({
            "source": source,
            "basename": base_name(source),
            "chunks": len(ids),
            "degree_min": min(degrees),
            "degree_max": max(degrees),
            "degree_mean": sum(degrees) / len(degrees),
            "weighted_degree_mean": sum(weighted) / len(weighted),
            "created_rank_min": min(ranks),
            "created_rank_max": max(ranks),
            "created_rank_mean": sum(ranks) / len(ranks),
        })
    return rows


def preview_text(corpus: FrozenCorpus, chunk_id: str, limit: int = 120) -> str:
    text = " ".join(str(corpus.records[chunk_id].text).split())
    return text[:limit]
