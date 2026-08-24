"""Read-only status projection.  This module never constructs an Organism."""
from __future__ import annotations

import time
from typing import Any, Dict

from .dynamics import KnowledgeDynamics
from .storage import Storage


def read_status(db_path: str) -> Dict[str, Any]:
    storage = Storage(db_path, read_only=True)
    try:
        chunks = storage.chunk_count()
        edges = storage.edge_count()
        connected = storage.connected_node_count()
        latest_rows = storage.recent_energy(limit=1)
        latest = latest_rows[0] if latest_rows else None
        runtime = storage.load_runtime_status()
        snapshot = storage.load_dynamics_snapshot()

        status_source = "empty"
        updated_at = None
        if runtime is not None and int(runtime.get("state_version", 0)) == KnowledgeDynamics.PERSISTENCE_VERSION:
            tick = int(runtime.get("tick", 0))
            stats = dict(runtime.get("dynamics_stats", runtime.get("void_stats", {})))
            settled = bool(runtime.get("settled", False))
            status_source = "runtime_status"
            updated_at = float(runtime.get("updated_at", 0.0))
        elif snapshot is not None and int(snapshot.get("version", 0)) == KnowledgeDynamics.PERSISTENCE_VERSION:
            dynamics = KnowledgeDynamics.from_dict(snapshot)
            tick = dynamics.tick_count
            stats = dynamics.stats()
            settled = bool(latest[7]) if latest is not None else False
            status_source = "dynamics_snapshot"
        else:
            tick = int(latest[0]) if latest is not None else 0
            stats = {
                "count": float(chunks),
                "territories": 0.0,
                "active": 0.0,
                "total_heat": 0.0,
                "avg_mass": 0.0,
                "avg_familiarity": 0.0,
                "tick": float(tick),
            }
            settled = bool(latest[7]) if latest is not None else False
            if chunks:
                status_source = "legacy_snapshot_requires_rebuild"

        daemon_state = storage.get_meta("daemon_state") or "unknown"
        age = None if updated_at is None else max(0.0, time.time() - updated_at)
        interval_raw = storage.get_meta("tick_interval_s")
        interval = float(interval_raw) if interval_raw else 3.0
        fresh = bool(daemon_state == "running" and age is not None and age <= max(10.0, interval * 3.0))
        snapshot_tick = int(snapshot.get("tick", 0)) if snapshot else None
        return {
            "tick": tick,
            "chunks": chunks,
            "graph_nodes": chunks,
            "connected_graph_nodes": connected,
            "graph_edges": edges,
            "dynamics_stats": stats,
            "latest_energy": latest,
            "settled": settled,
            "pending_stimuli": storage.pending_stimuli_count(),
            "watch_folder_path": storage.get_meta("watch_folder_path"),
            "watch_folder_ok": _optional_bool(storage.get_meta("watch_folder_ok")),
            "embedding_model": storage.get_meta("embedding_model"),
            "embedding_dim": _optional_int(storage.get_meta("embedding_dim")),
            "daemon_state": daemon_state,
            "runtime_fresh": fresh,
            "runtime_age_s": age,
            "status_source": status_source,
            "status_updated_at": updated_at,
            "snapshot_tick": snapshot_tick,
        }
    finally:
        storage.close()


def _optional_bool(value: str | None) -> bool | None:
    return None if value is None else value == "1"


def _optional_int(value: str | None) -> int | None:
    return None if value is None else int(value)


__all__ = ["read_status"]
