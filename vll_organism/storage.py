"""SQLite persistence for durable corpus truth and small runtime projections."""
from __future__ import annotations

import array
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple


SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    hash TEXT NOT NULL,
    source TEXT,
    created_at REAL NOT NULL,
    embedding BLOB,
    embedding_dim INTEGER,
    territory INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(hash);
CREATE INDEX IF NOT EXISTS idx_chunks_territory ON chunks(territory);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);

CREATE TABLE IF NOT EXISTS chunk_sources (
    chunk_id TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (chunk_id, source),
    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunk_sources_source ON chunk_sources(source);

CREATE TABLE IF NOT EXISTS edges (
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight REAL NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (source, target, relation)
);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);

CREATE TABLE IF NOT EXISTS dynamics_snapshot (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    tick INTEGER NOT NULL,
    data TEXT NOT NULL,
    saved_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS void_snapshot (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    tick INTEGER NOT NULL,
    data TEXT NOT NULL,
    saved_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tick INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload TEXT,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS stimuli (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    strength REAL NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stimuli_id ON stimuli(id);

CREATE TABLE IF NOT EXISTS energy_log (
    tick INTEGER PRIMARY KEY,
    heat_energy REAL NOT NULL,
    mass_variance_energy REAL NOT NULL,
    total_energy REAL NOT NULL,
    delta_frac REAL,
    structural_speed REAL,
    topo_events INTEGER NOT NULL,
    settled INTEGER NOT NULL,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    tick INTEGER NOT NULL,
    data TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


@dataclass
class ChunkRecord:
    id: str
    text: str
    hash: str
    source: Optional[str]
    created_at: float
    embedding: Optional[array.array]
    territory: Optional[int] = None
    sources: Tuple[str, ...] = ()


class Storage:
    def __init__(self, path: str, read_only: bool = False, initialize: bool = True):
        self.path = path
        self.read_only = bool(read_only)
        self.initialize = bool(initialize)
        if not self.read_only and not self.initialize and not os.path.isfile(path):
            raise RuntimeError(f"database does not exist: {os.path.abspath(path)!r}")
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent) and not self.read_only:
            os.makedirs(parent, exist_ok=True)
        try:
            if self.read_only:
                uri = f"file:{os.path.abspath(path)}?mode=ro"
                self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=5.0)
            else:
                self._conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._conn.execute("PRAGMA busy_timeout=5000")
                if self.initialize:
                    self._conn.execute("PRAGMA journal_mode=WAL")
                    self._migrate_legacy_schema()
                    self._conn.executescript(SCHEMA)
                    self._backfill_chunk_sources()
                    self._conn.commit()
        except sqlite3.OperationalError as exc:
            mode = "read" if self.read_only else "open"
            raise RuntimeError(
                f"Could not {mode} database at {path!r} (resolved: {os.path.abspath(path)!r}): {exc}."
            ) from exc
        if self.read_only:
            self._conn.execute("PRAGMA busy_timeout=5000")

    def close(self) -> None:
        self._conn.close()

    def _migrate_legacy_schema(self) -> None:
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks'"
        ).fetchone()
        if not exists:
            return
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(chunks)")}
        if "territory" not in cols:
            self._conn.execute("ALTER TABLE chunks ADD COLUMN territory INTEGER")

    def _backfill_chunk_sources(self) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO chunk_sources(chunk_id, source) "
            "SELECT id, source FROM chunks WHERE source IS NOT NULL AND source <> ''"
        )

    # ---------------- Chunks / provenance ----------------
    def find_chunk_by_hash(self, chunk_hash: str) -> Optional[ChunkRecord]:
        row = self._conn.execute(
            _CHUNK_SELECT + " WHERE hash = ? ORDER BY created_at LIMIT 1", (chunk_hash,)
        ).fetchone()
        return self._row_to_chunk(row) if row else None

    def put_chunk(
        self,
        chunk_id: str,
        text: str,
        chunk_hash: str,
        source: Optional[str],
        embedding: Optional[Sequence[float]],
        territory: Optional[int] = None,
    ) -> None:
        blob = _floats_to_blob(embedding) if embedding is not None else None
        dim = len(embedding) if embedding is not None else None
        now = time.time()
        with self._conn:
            self._conn.execute(
                "INSERT INTO chunks(id,text,hash,source,created_at,embedding,embedding_dim,territory) "
                "VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET text=excluded.text, hash=excluded.hash, "
                "embedding=excluded.embedding, embedding_dim=excluded.embedding_dim, "
                "territory=excluded.territory",
                (chunk_id, text, chunk_hash, source, now, blob, dim, territory),
            )
            if source:
                self._conn.execute(
                    "INSERT OR IGNORE INTO chunk_sources(chunk_id,source) VALUES(?,?)",
                    (chunk_id, source),
                )

    def attach_source(self, chunk_id: str, source: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO chunk_sources(chunk_id,source) VALUES(?,?)", (chunk_id, source)
            )

    def source_chunk_ids(self, source: str) -> set[str]:
        return {row[0] for row in self._conn.execute(
            "SELECT chunk_id FROM chunk_sources WHERE source=?", (source,)
        ).fetchall()}

    def sync_source(self, source: str, current_chunk_ids: Iterable[str]) -> List[str]:
        """Detach stale chunks from a changed source and delete true orphans.

        Returns orphan chunk ids removed from durable storage.  Callers remove
        those ids from graph/dynamics using embeddings captured beforehand.
        """
        current = set(current_chunk_ids)
        existing = {
            row[0]
            for row in self._conn.execute(
                "SELECT chunk_id FROM chunk_sources WHERE source = ?", (source,)
            ).fetchall()
        }
        stale = existing - current
        if not stale:
            return []
        orphaned: List[str] = []
        with self._conn:
            self._conn.executemany(
                "DELETE FROM chunk_sources WHERE chunk_id=? AND source=?",
                [(chunk_id, source) for chunk_id in stale],
            )
            for chunk_id in stale:
                refs = self._conn.execute(
                    "SELECT 1 FROM chunk_sources WHERE chunk_id=? LIMIT 1", (chunk_id,)
                ).fetchone()
                if refs is None:
                    orphaned.append(chunk_id)
            self._delete_chunks_sql(orphaned)
        return orphaned

    def get_chunk(self, chunk_id: str) -> Optional[ChunkRecord]:
        row = self._conn.execute(_CHUNK_SELECT + " WHERE id = ?", (chunk_id,)).fetchone()
        return self._row_to_chunk(row) if row else None

    def get_chunks(self, chunk_ids: Sequence[str]) -> List[ChunkRecord]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self._conn.execute(
            _CHUNK_SELECT + f" WHERE id IN ({placeholders})", tuple(chunk_ids)
        ).fetchall()
        by_id = {row[0]: self._row_to_chunk(row) for row in rows}
        return [by_id[cid] for cid in chunk_ids if cid in by_id]

    def all_chunks(self) -> List[ChunkRecord]:
        return [self._row_to_chunk(row) for row in self._conn.execute(_CHUNK_SELECT).fetchall()]

    def chunk_headers(self) -> List[Tuple[str, float, Optional[int]]]:
        return [
            (row[0], float(row[1]), None if row[2] is None else int(row[2]))
            for row in self._conn.execute("SELECT id, created_at, territory FROM chunks ORDER BY created_at,id")
        ]

    def candidate_chunks(self, territories: Sequence[int], limit: int) -> List[ChunkRecord]:
        cap = max(1, int(limit))
        ordered_territories = list(dict.fromkeys(int(t) for t in territories))
        if not ordered_territories:
            rows = self._conn.execute(
                _CHUNK_SELECT + " ORDER BY created_at DESC LIMIT ?", (cap,)
            ).fetchall()
            return [self._row_to_chunk(row) for row in rows]

        # Give each selected semantic territory representation instead of
        # letting one large/recent territory consume the entire candidate cap.
        quota = max(1, (cap + len(ordered_territories) - 1) // len(ordered_territories))
        rows = []
        for territory in ordered_territories:
            rows.extend(
                self._conn.execute(
                    _CHUNK_SELECT + " WHERE territory=? ORDER BY created_at DESC LIMIT ?",
                    (territory, quota),
                ).fetchall()
            )
        return [self._row_to_chunk(row) for row in rows[:cap]]

    def get_chunk_sources(self, chunk_id: str) -> Tuple[str, ...]:
        rows = self._conn.execute(
            "SELECT source FROM chunk_sources WHERE chunk_id=? ORDER BY source", (chunk_id,)
        ).fetchall()
        return tuple(row[0] for row in rows)

    def set_chunk_territory(self, chunk_id: str, territory: int) -> None:
        with self._conn:
            self._conn.execute("UPDATE chunks SET territory=? WHERE id=?", (int(territory), chunk_id))

    def chunk_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

    def delete_chunks(self, chunk_ids: Iterable[str]) -> None:
        with self._conn:
            self._delete_chunks_sql(list(chunk_ids))

    def _delete_chunks_sql(self, chunk_ids: Sequence[str]) -> None:
        for chunk_id in chunk_ids:
            self._conn.execute("DELETE FROM edges WHERE source=? OR target=?", (chunk_id, chunk_id))
            self._conn.execute("DELETE FROM stimuli WHERE chunk_id=?", (chunk_id,))
            self._conn.execute("DELETE FROM chunk_sources WHERE chunk_id=?", (chunk_id,))
            self._conn.execute("DELETE FROM chunks WHERE id=?", (chunk_id,))

    # ---------------- Graph ----------------
    def put_edge(self, source: str, target: str, relation: str, weight: float) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO edges(source,target,relation,weight,created_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(source,target,relation) DO UPDATE SET weight=excluded.weight",
                (source, target, relation, float(weight), time.time()),
            )

    def all_edges(self) -> List[Tuple[str, str, str, float]]:
        return [tuple(row) for row in self._conn.execute(
            "SELECT source,target,relation,weight FROM edges"
        ).fetchall()]

    def edge_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0])

    def connected_node_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM (SELECT source AS id FROM edges UNION SELECT target AS id FROM edges)"
        ).fetchone()
        return int(row[0])

    def weighted_neighbors(self, node_id: str, limit: int = 12) -> List[Tuple[str, float]]:
        """Read strongest local neighbors without loading/scanning the graph."""
        cap = max(0, int(limit))
        if cap == 0:
            return []
        outgoing = self._conn.execute(
            "SELECT target,weight FROM edges WHERE source=? ORDER BY weight DESC LIMIT ?",
            (node_id, cap),
        ).fetchall()
        incoming = self._conn.execute(
            "SELECT source,weight FROM edges WHERE target=? ORDER BY weight DESC LIMIT ?",
            (node_id, cap),
        ).fetchall()
        combined = {}
        for neighbor, weight in outgoing + incoming:
            combined[neighbor] = max(combined.get(neighbor, 0.0), float(weight))
        return sorted(combined.items(), key=lambda item: item[1], reverse=True)[:cap]

    # ---------------- Dynamics snapshots ----------------
    def save_dynamics_snapshot(self, tick: int, data: dict) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO dynamics_snapshot(id,tick,data,saved_at) VALUES(1,?,?,?)",
                (int(tick), json.dumps(data), time.time()),
            )

    def load_dynamics_snapshot(self) -> Optional[dict]:
        try:
            row = self._conn.execute("SELECT data FROM dynamics_snapshot WHERE id=1").fetchone()
        except sqlite3.OperationalError:
            return None
        return json.loads(row[0]) if row else None

    # Legacy aliases retained for migration/tests.
    def save_void_snapshot(self, tick: int, data: dict) -> None:
        self.save_dynamics_snapshot(tick, data)

    def load_void_snapshot(self) -> Optional[dict]:
        current = self.load_dynamics_snapshot()
        if current is not None:
            return current
        try:
            row = self._conn.execute("SELECT data FROM void_snapshot WHERE id=1").fetchone()
        except sqlite3.OperationalError:
            return None
        return json.loads(row[0]) if row else None

    # ---------------- Meta / history / stimuli ----------------
    def set_meta(self, key: str, value: Optional[str]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value)
            )

    def get_meta(self, key: str) -> Optional[str]:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def log_event(self, tick: int, event_type: str, payload: dict) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO events(tick,type,payload,ts) VALUES(?,?,?,?)",
                (int(tick), event_type, json.dumps(payload), time.time()),
            )

    def recent_events(self, limit: int = 100):
        rows = self._conn.execute(
            "SELECT id,tick,type,payload,ts FROM events ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [(rid, tick, etype, json.loads(payload) if payload else {}, ts) for rid,tick,etype,payload,ts in rows]

    def enqueue_stimuli(self, stimuli: Iterable[Tuple[str, float]], stimulus_type: str = "query") -> int:
        rows = [(stimulus_type, cid, float(strength), time.time()) for cid, strength in stimuli]
        if not rows:
            return 0
        with self._conn:
            self._conn.executemany(
                "INSERT INTO stimuli(type,chunk_id,strength,ts) VALUES(?,?,?,?)", rows
            )
        return len(rows)

    def pending_stimuli(self, limit: int = 256) -> List[Tuple[int, str, str, float]]:
        rows = self._conn.execute(
            "SELECT id,type,chunk_id,strength FROM stimuli ORDER BY id LIMIT ?", (int(limit),)
        ).fetchall()
        return [(int(r[0]), r[1], r[2], float(r[3])) for r in rows]

    def delete_stimuli(self, stimulus_ids: Sequence[int]) -> None:
        if not stimulus_ids:
            return
        with self._conn:
            self._conn.executemany("DELETE FROM stimuli WHERE id=?", [(int(i),) for i in stimulus_ids])

    def pending_stimuli_count(self) -> int:
        try:
            return int(self._conn.execute("SELECT COUNT(*) FROM stimuli").fetchone()[0])
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return 0
            raise

    # ---------------- Energy / live status ----------------
    def log_energy(self, tick: int, heat_energy: float, mass_variance_energy: float,
                   total_energy: float, delta_frac: Optional[float],
                   structural_speed: Optional[float], topo_events: int,
                   settled: bool) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO energy_log(tick,heat_energy,mass_variance_energy,total_energy,"
                "delta_frac,structural_speed,topo_events,settled,ts) VALUES(?,?,?,?,?,?,?,?,?)",
                (int(tick), float(heat_energy), float(mass_variance_energy), float(total_energy),
                 None if delta_frac is None else float(delta_frac),
                 None if structural_speed is None else float(structural_speed),
                 int(topo_events), int(settled), time.time()),
            )

    def recent_energy(self, limit: int = 200):
        rows = self._conn.execute(
            "SELECT tick,heat_energy,mass_variance_energy,total_energy,delta_frac,structural_speed,"
            "topo_events,settled FROM energy_log ORDER BY tick DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [(r[0],r[1],r[2],r[3],r[4],r[5],r[6],bool(r[7])) for r in rows]

    def save_runtime_status(self, tick: int, data: dict) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO runtime_status(id,tick,data,updated_at) VALUES(1,?,?,?)",
                (int(tick), json.dumps(data), time.time()),
            )

    def load_runtime_status(self) -> Optional[dict]:
        try:
            row = self._conn.execute("SELECT tick,data,updated_at FROM runtime_status WHERE id=1").fetchone()
        except sqlite3.OperationalError:
            return None
        if not row:
            return None
        payload = json.loads(row[1])
        payload["tick"] = int(row[0])
        payload["updated_at"] = float(row[2])
        return payload

    def _row_to_chunk(self, row) -> ChunkRecord:
        cid, text, chash, source, created_at, blob, dim, territory = row
        embedding = _blob_to_array(blob, dim) if blob is not None else None
        return ChunkRecord(
            id=cid,
            text=text,
            hash=chash,
            source=source,
            created_at=float(created_at),
            embedding=embedding,
            territory=None if territory is None else int(territory),
            sources=self.get_chunk_sources(cid),
        )


_CHUNK_SELECT = "SELECT id,text,hash,source,created_at,embedding,embedding_dim,territory FROM chunks"


def _floats_to_blob(values: Sequence[float]) -> bytes:
    return array.array("f", values).tobytes()


def _blob_to_array(blob: bytes, dim: Optional[int]) -> array.array:
    values = array.array("f")
    values.frombytes(blob)
    if dim is not None and len(values) != int(dim):
        raise ValueError(f"stored embedding dimension mismatch: blob={len(values)} metadata={dim}")
    return values


__all__ = ["Storage", "ChunkRecord"]
