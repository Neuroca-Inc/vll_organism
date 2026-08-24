import json
import os
import sqlite3
import tempfile

import pytest

from vll_organism.dynamics import KnowledgeDynamics
from vll_organism.embedder import EmbeddingError, HashEmbedder
from vll_organism.organism import Organism, OrganismConfig
from vll_organism.retrieval import QueryConfig, RetrievalEngine
from vll_organism.storage import Storage


def _write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def test_query_is_useful_and_feedback_is_applied_by_daemon_owner():
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "organism.db")
        source = os.path.join(td, "knowledge.md")
        _write(
            source,
            "Sparse graph diffusion moves transient activation between related knowledge chunks.\n\n"
            "Sourdough fermentation uses a starter culture and a long proofing period.\n\n"
            "Semantic territories provide a bounded candidate surface for retrieval.",
        )
        organism = Organism(
            OrganismConfig(db_path=db, max_chunk_chars=110),
            HashEmbedder(dim=64),
        )
        organism.perturb_file(source)
        query_vec = organism.embedder.embed("semantic graph retrieval territory")
        hits = RetrievalEngine(organism.storage, organism.void, organism.graph).query(
            query_vec, QueryConfig(top_k=2, candidate_cap=16, graph_candidate_cap=16)
        )
        assert hits
        target = hits[0].chunk_id
        before = organism.void.get_state(target).use_count
        before_tick = organism.void.tick_count
        organism.storage.enqueue_stimuli([(target, 0.9)])
        assert organism.void.get_state(target).use_count == before
        organism.idle_tick()
        assert organism.void.tick_count == before_tick + 1
        assert organism.void.get_state(target).use_count == before + 1
        organism.stop()


def test_changed_file_replaces_stale_orphan_chunks_instead_of_accumulating_versions():
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "organism.db")
        source = os.path.join(td, "note.md")
        organism = Organism(OrganismConfig(db_path=db, max_chunk_chars=90), HashEmbedder(dim=64))
        _write(source, "alpha mechanics local topology " * 8)
        first = organism.perturb_file(source)
        first_ids = organism.storage.source_chunk_ids(os.path.abspath(source))
        assert first.new_chunks and first_ids

        _write(source, "completely revised biology fermentation culture " * 8)
        second = organism.perturb_file(source)
        second_ids = organism.storage.source_chunk_ids(os.path.abspath(source))
        assert second.new_chunks > 0
        assert second.removed_stale_chunks == len(first_ids)
        assert not (first_ids & second_ids)
        assert organism.storage.chunk_count() == len(second_ids)
        organism.stop()


def test_global_content_dedup_preserves_all_source_provenance():
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "organism.db")
        a = os.path.join(td, "a.md")
        b = os.path.join(td, "b.md")
        shared = "the exact same durable knowledge appears in two source documents"
        _write(a, shared)
        _write(b, shared)
        organism = Organism(OrganismConfig(db_path=db), HashEmbedder(dim=64))
        assert organism.perturb_file(a).new_chunks == 1
        assert organism.perturb_file(b).new_chunks == 0
        assert organism.storage.chunk_count() == 1
        record = organism.storage.all_chunks()[0]
        assert set(record.sources) == {os.path.abspath(a), os.path.abspath(b)}

        _write(a, "a now contains replacement knowledge")
        result = organism.perturb_file(a)
        assert result.removed_stale_chunks == 0, "shared old content is still owned by b.md"
        old = organism.storage.find_chunk_by_hash(record.hash)
        assert old is not None
        assert old.sources == (os.path.abspath(b),)
        organism.stop()


def test_legacy_empty_snapshot_is_rebuilt_from_durable_chunks():
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "organism.db")
        embedder = HashEmbedder(dim=32)
        storage = Storage(db)
        emb = embedder.embed("durable chunk survives a damaged dynamics snapshot")
        storage.put_chunk("legacy:1", "durable chunk survives", "hash-1", "/tmp/source.md", emb)
        storage.close()
        with sqlite3.connect(db) as con:
            con.execute(
                "INSERT OR REPLACE INTO void_snapshot(id,tick,data,saved_at) VALUES(1,5,?,0)",
                (json.dumps({"version": 2, "tick": 5, "mem": {}}),),
            )
            con.commit()

        organism = Organism(OrganismConfig(db_path=db), embedder)
        assert organism.storage.chunk_count() == 1
        assert organism.void.ids() == {"legacy:1"}
        assert organism.void.tick_count == 5
        state = organism.void.get_state("legacy:1")
        assert state is not None and state.heat == 0.0
        assert organism.storage.get_chunk("legacy:1").territory is not None
        organism.stop()


def test_same_dimension_different_embedding_model_is_rejected():
    class NamedHash(HashEmbedder):
        def __init__(self, name):
            super().__init__(dim=32)
            self.model_id = name

    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "organism.db")
        first_path = os.path.join(td, "first.md")
        second_path = os.path.join(td, "second.md")
        _write(first_path, "first corpus chunk")
        _write(second_path, "second distinct corpus chunk")
        first = Organism(OrganismConfig(db_path=db), NamedHash("model:a"))
        first.perturb_file(first_path)
        first.stop()

        second = Organism(OrganismConfig(db_path=db), NamedHash("model:b"))
        with pytest.raises(RuntimeError, match="embedding model changed"):
            second.perturb_file(second_path)
        second.stop()


def test_daemon_does_not_hold_all_embeddings_in_python_object_lists():
    with tempfile.TemporaryDirectory() as td:
        organism = Organism(
            OrganismConfig(db_path=os.path.join(td, "organism.db")),
            HashEmbedder(dim=64),
        )
        assert not hasattr(organism, "_embeddings")
        organism.stop()


def test_ingestion_cardinality_does_not_advance_time_or_expire_knowledge():
    dynamics = KnowledgeDynamics()
    for index in range(300):
        vec = [0.0] * 8
        vec[index % 8] = 1.0
        dynamics.register_chunk(f"chunk-{index}", float(index), vec, cold=True)
    assert dynamics.tick_count == 0
    assert len(dynamics.ids()) == 300
    assert all(state is not None for state in (dynamics.get_state(f"chunk-{i}") for i in range(300)))


def test_territories_are_capacity_bounded_candidate_surfaces():
    from vll_organism.dynamics import DynamicsConfig

    dynamics = KnowledgeDynamics(
        DynamicsConfig(territory_similarity_threshold=-1.0, territory_max_members=4)
    )
    for index in range(11):
        dynamics.register_chunk(f"chunk-{index}", float(index), [1.0, 0.0, 0.0], cold=True)
    counts = {}
    for state in dynamics.iter_states():
        counts[state.territory] = counts.get(state.territory, 0) + 1
    assert sorted(counts.values(), reverse=True) == [4, 4, 3]


def test_only_one_organism_writer_can_own_a_database():
    from vll_organism.writer_lock import WriterLockError

    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "organism.db")
        first = Organism(OrganismConfig(db_path=db), HashEmbedder(dim=32))
        with pytest.raises(WriterLockError, match="active organism writer"):
            Organism(OrganismConfig(db_path=db), HashEmbedder(dim=32))
        # Query/status-style lightweight SQLite writers are deliberately still
        # allowed to enqueue feedback while the daemon owns organism state.
        side = Storage(db, initialize=False)
        assert side.enqueue_stimuli([("missing-id-is-safe", 0.5)]) == 1
        side.close()
        first.idle_tick()
        assert first.storage.pending_stimuli_count() == 0
        first.stop()


def test_candidate_pool_represents_each_selected_territory():
    with tempfile.TemporaryDirectory() as td:
        storage = Storage(os.path.join(td, "organism.db"))
        for territory in (1, 2, 3):
            for index in range(5):
                storage.put_chunk(
                    f"t{territory}-{index}",
                    f"territory {territory} item {index}",
                    f"h-{territory}-{index}",
                    f"/tmp/t{territory}.md",
                    [1.0, 0.0],
                    territory=territory,
                )
        candidates = storage.candidate_chunks([1, 2, 3], limit=6)
        represented = {record.territory for record in candidates}
        assert represented == {1, 2, 3}
        assert len(candidates) == 6
        storage.close()


def test_query_can_expand_graph_without_global_edge_load():
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "organism.db")
        source = os.path.join(td, "corpus.txt")
        _write(source, "alpha beta gamma delta epsilon zeta eta theta")
        organism = Organism(
            OrganismConfig(db_path=db, max_chunk_chars=80, similarity_threshold=-1.0),
            HashEmbedder(dim=32),
        )
        organism.perturb_file(source)
        query_vec = organism.embedder.embed("alpha beta")
        organism.storage.all_edges = lambda: (_ for _ in ()).throw(AssertionError("global edge load"))
        hits = RetrievalEngine(organism.storage, organism.void).query(
            query_vec, QueryConfig(top_k=1, candidate_cap=8, graph_hops=1, graph_candidate_cap=8)
        )
        assert hits
        organism.stop()


class _MarkerFailEmbedder:
    def __init__(self, dim=32):
        self._base = HashEmbedder(dim=dim)
        self.dim = dim
        self.model_id = "marker-fail:test"

    def embed(self, text):
        if "TRANSIENT_FAIL" in text:
            raise EmbeddingError("simulated mid-file outage")
        return self._base.embed(text)


def test_incomplete_file_replacement_rolls_back_partial_new_version():
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "organism.db")
        source = os.path.join(td, "knowledge.md")
        embedder = _MarkerFailEmbedder()
        organism = Organism(OrganismConfig(db_path=db, max_chunk_chars=100), embedder)
        _write(source, "known good durable version")
        assert organism.perturb_file(source).new_chunks == 1
        old_ids = organism.storage.source_chunk_ids(os.path.abspath(source))
        old_count = organism.storage.chunk_count()

        _write(
            source,
            "new chunk that embeds successfully before the later failure"
            + "\n\nTRANSIENT_FAIL this second chunk simulates Ollama going away",
        )
        result = organism.perturb_file(source)
        assert result.retry_required is True
        assert result.new_chunks == 0
        assert organism.storage.source_chunk_ids(os.path.abspath(source)) == old_ids
        assert organism.storage.chunk_count() == old_count
        assert all("new chunk" not in rec.text for rec in organism.storage.all_chunks())
        organism.stop()


class _BlockingEmbedder:
    dim = 32
    model_id = "blocking:test"

    def __init__(self):
        import threading
        self.entered = threading.Event()
        self.release = threading.Event()

    def embed(self, _text):
        self.entered.set()
        self.release.wait()
        return [1.0] + [0.0] * (self.dim - 1)


def test_shutdown_never_closes_storage_under_live_watcher():
    import threading
    import time

    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "organism.db")
        watch = os.path.join(td, "corpus_drop")
        os.makedirs(watch)
        _write(os.path.join(watch, "slow.txt"), "slow embedding input")
        embedder = _BlockingEmbedder()
        organism = Organism(
            OrganismConfig(db_path=db, shutdown_grace_s=0.05), embedder
        )
        organism.start_watching(watch, poll_interval_s=0.01)
        assert embedder.entered.wait(timeout=1.0)

        with pytest.raises(RuntimeError, match="watcher did not stop"):
            organism.stop()
        # Failed shutdown must retain the DB and writer lease rather than close
        # them while the watcher can still resume.
        assert organism.storage.chunk_count() == 0

        embedder.release.set()
        organism._watch_thread.join(timeout=1.0)
        assert not organism._watch_thread.is_alive()
        organism.stop()


def test_noninitializing_query_storage_never_creates_a_database():
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "missing.db")
        with pytest.raises(RuntimeError, match="database does not exist"):
            Storage(db, initialize=False)
        assert not os.path.exists(db)
