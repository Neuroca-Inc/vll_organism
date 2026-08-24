"""Regressions for failures found on the real long-running daemon path."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

from vll_organism.embedder import HashEmbedder
from vll_organism.organism import Organism, OrganismConfig
from vll_organism.status import read_status

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


class SlowEmbedder:
    dim = 32

    def embed(self, text: str):
        time.sleep(0.20)
        return [1.0] + [0.0] * (self.dim - 1)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _snapshot_row(db_path: str):
    with sqlite3.connect(db_path) as con:
        return con.execute(
            "SELECT tick, data, saved_at FROM dynamics_snapshot WHERE id = 1"
        ).fetchone()


def _energy_count(db_path: str) -> int:
    with sqlite3.connect(db_path) as con:
        return int(con.execute("SELECT COUNT(*) FROM energy_log").fetchone()[0])


def test_status_cli_is_strictly_non_destructive():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=32))
        organism.idle_tick()
        organism.stop()
        before = _snapshot_row(db_path)
        before_hash = _sha256(db_path)
        assert before is not None

        result = subprocess.run(
            [sys.executable, "-m", "vll_organism", "status", "--db", db_path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        parsed = json.loads(result.stdout)
        after = _snapshot_row(db_path)
        after_hash = _sha256(db_path)

        assert after == before, "status must not rewrite or resave organism state"
        assert after_hash == before_hash, "status must not modify database bytes"
        assert parsed["tick"] >= 1


def test_slow_embedding_does_not_starve_idle_ticks():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        corpus = os.path.join(td, "corpus.txt")
        with open(corpus, "w", encoding="utf-8") as f:
            f.write("\n\n".join(f"chunk {i} " + ("x" * 110) for i in range(6)))

        organism = Organism(
            OrganismConfig(
                db_path=db_path,
                tick_interval_s=0.05,
                max_chunk_chars=140,
                snapshot_every_ticks=1000,
            ),
            SlowEmbedder(),
        )
        organism.start()
        time.sleep(0.12)
        before = _energy_count(db_path)

        worker = threading.Thread(target=organism.perturb_file, args=(corpus,))
        worker.start()
        time.sleep(0.50)
        during = _energy_count(db_path)
        worker.join(timeout=5)
        organism.stop()

        assert during >= before + 3, (
            "idle energy samples should keep advancing while an external embedding call is slow"
        )


def test_status_counts_isolated_chunks_as_graph_nodes():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        corpus = os.path.join(td, "single.txt")
        with open(corpus, "w", encoding="utf-8") as f:
            f.write("one isolated knowledge chunk")

        organism = Organism(
            OrganismConfig(db_path=db_path, similarity_threshold=1.0),
            HashEmbedder(dim=32),
        )
        result = organism.perturb_file(corpus)
        assert result.new_chunks == 1
        organism.stop()

        status = read_status(db_path)
        assert status["chunks"] == 1
        assert status["graph_nodes"] == 1
        assert status["connected_graph_nodes"] == 0
        assert status["graph_edges"] == 0


def test_dynamics_snapshot_restores_non_default_config():
    from vll_organism.dynamics import DynamicsConfig, KnowledgeDynamics

    original = KnowledgeDynamics(DynamicsConfig(
        heat_half_life_ticks=33,
        registration_heat_gain=4.5,
        diffusion_fraction=0.12,
        active_budget=321,
        territory_similarity_threshold=0.61,
    ))
    restored = KnowledgeDynamics.from_dict(original.to_dict())

    assert restored.config.heat_half_life_ticks == 33
    assert restored.config.registration_heat_gain == 4.5
    assert restored.config.diffusion_fraction == 0.12
    assert restored.config.active_budget == 321
    assert restored.config.territory_similarity_threshold == 0.61
