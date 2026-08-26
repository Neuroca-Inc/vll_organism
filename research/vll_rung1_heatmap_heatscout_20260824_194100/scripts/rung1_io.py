"""Snapshot, corpus, output, and provenance helpers for VLL Rung 1."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Sequence

@dataclass(frozen=True)
class FrozenCorpus:
    db_path: str
    records: dict[str, object]
    ids: tuple[str, ...]
    graph: object
    dynamics_config: object
    snapshot_tick: int
    source_sets: dict[str, frozenset[str]]
    primary_sources: dict[str, str]
    source_names: tuple[str, ...]
    meta: dict[str, str]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def write_csv(path: Path, rows: Sequence[dict], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_csv_gz(path: Path, rows: Sequence[dict], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=30.0)
    dst = sqlite3.connect(str(destination), timeout=30.0)
    try:
        src.execute("PRAGMA busy_timeout=30000")
        src.backup(dst, pages=256, sleep=0.01)
        dst.commit()
        # Keep the experiment snapshot self-contained as one SQLite file.
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        dst.close()
        src.close()


def db_integrity(path: Path) -> dict:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return {
            "quick_check": [r[0] for r in conn.execute("PRAGMA quick_check")],
            "foreign_key_violations": [list(r) for r in conn.execute("PRAGMA foreign_key_check")],
        }
    finally:
        conn.close()


def load_frozen_corpus(db_path: Path) -> FrozenCorpus:
    from vll_organism.dynamics import DynamicsConfig, KnowledgeDynamics
    from vll_organism.graph import KnowledgeGraph
    from vll_organism.storage import Storage

    storage = Storage(str(db_path), read_only=True, initialize=False)
    try:
        records_list = storage.all_chunks()
        snapshot = storage.load_dynamics_snapshot()
        edges = storage.all_edges()
        meta = {}
        for key in ("embedding_dim", "embedding_model", "tick_interval_s", "daemon_state"):
            value = storage.get_meta(key)
            if value is not None:
                meta[key] = value
        source_sets = {
            r.id: frozenset(storage.get_chunk_sources(r.id) or (() if not r.source else (r.source,)))
            for r in records_list
        }
    finally:
        storage.close()

    usable = [r for r in records_list if r.embedding is not None]
    if not usable:
        raise RuntimeError("frozen snapshot contains no embedded chunks")
    ids = tuple(r.id for r in usable)
    id_set = set(ids)
    records = {r.id: r for r in usable}
    bad = [(s, t) for s, t, _rel, _w in edges if s not in id_set or t not in id_set]
    if bad:
        raise RuntimeError(f"graph has edge endpoints without usable chunks: {bad[:5]}")
    graph = KnowledgeGraph()
    graph.load_edge_list(edges)
    for cid in ids:
        graph.add_node(cid)
    if snapshot is None or int(snapshot.get("version", 0)) != KnowledgeDynamics.PERSISTENCE_VERSION:
        raise RuntimeError("no compatible dynamics snapshot; refusing to substitute current defaults")
    config = DynamicsConfig(**dict(snapshot.get("config", {})))
    primary = {r.id: (r.source or "") for r in usable}
    names = sorted({src for values in source_sets.values() for src in values if src})
    return FrozenCorpus(
        str(db_path.resolve()), records, ids, graph, config, int(snapshot.get("tick", 0)),
        source_sets, primary, tuple(names), meta,
    )


def base_name(path: str | None) -> str:
    return os.path.basename(path or "")


def resolve_source(corpus: FrozenCorpus, selector: str) -> str:
    if selector in corpus.source_names:
        return selector
    matches = [src for src in corpus.source_names if base_name(src) == selector]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"source {selector!r} not found in frozen corpus")
    raise ValueError(f"source basename {selector!r} is ambiguous: {matches}")


def ids_for_source(corpus: FrozenCorpus, source: str) -> list[str]:
    return sorted(cid for cid, sources in corpus.source_sets.items() if source in sources)


def clean_template(corpus: FrozenCorpus) -> dict:
    from vll_organism.dynamics import KnowledgeDynamics
    d = KnowledgeDynamics(corpus.dynamics_config)
    for cid in sorted(corpus.ids, key=lambda x: (float(corpus.records[x].created_at), x)):
        r = corpus.records[cid]
        d.register_chunk(cid, r.created_at, r.embedding, cold=True)
    zero_heat(d)
    return d.to_dict()


def zero_heat(dynamics: object) -> None:
    dynamics._active_queue.clear()
    dynamics._active_set.clear()
    dynamics._total_heat = 0.0
    for state in dynamics.iter_states():
        state.heat = 0.0
        state.last_decay_tick = dynamics.tick_count


def pulse(dynamics: object, node_id: str, amount: float) -> None:
    state = dynamics.get_state(node_id)
    if state is None:
        raise RuntimeError(f"pulse target is absent: {node_id}")
    state.heat += float(amount)
    state.last_decay_tick = dynamics.tick_count
    dynamics._total_heat += float(amount)
    dynamics._activate(node_id)


def hash_tree(root: Path, *, exclude_names: set[str] | None = None) -> list[dict]:
    exclude_names = exclude_names or set()
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name in exclude_names or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        rows.append({
            "path": str(path.relative_to(root)),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return rows
