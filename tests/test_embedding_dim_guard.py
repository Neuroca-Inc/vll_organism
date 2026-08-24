"""Regression coverage for the embedding-dimension consistency guard.

Directly motivated: graph.py's cosine similarity does `zip(a, b)`, which
silently truncates to the shorter vector on a length mismatch instead of
raising. Without a guard, switching --embed-model (and therefore output
dimension) on an existing --db wouldn't crash -- it would just quietly
start computing meaningless similarity scores and territory assignments,
with nothing in the logs to explain why. This matters concretely: a user
running the daemon against a real, populated database who then switches
to a higher-quality/larger-context embedding model needs to be stopped
loudly, not silently corrupted.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vll_organism.embedder import HashEmbedder
from vll_organism.organism import Organism, OrganismConfig


def _write(dirpath, name, content):
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def test_first_ingest_records_the_dimension_baseline():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        path = _write(td, "a.txt", "some corpus text about territories and homeostasis")
        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        organism.perturb_file(path)
        assert organism.storage.get_meta("embedding_dim") == "64"
        organism.stop()


def test_same_dimension_across_restarts_is_fine():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        path = _write(td, "a.txt", "some corpus text about territories and homeostasis")
        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        organism.perturb_file(path)
        organism.stop()

        # Fresh process, same dim -- must ingest normally, no error.
        organism2 = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        path2 = _write(td, "b.txt", "a second file about the same subject matter entirely")
        result = organism2.perturb_file(path2)
        assert result.new_chunks > 0
        organism2.stop()


def test_switching_dimension_on_existing_db_raises_instead_of_corrupting():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        path = _write(td, "a.txt", "some corpus text about territories and homeostasis")
        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        organism.perturb_file(path)
        organism.stop()

        # Fresh process, DIFFERENT dim (simulating a model switch on the same db).
        organism2 = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=128))
        path2 = _write(td, "b.txt", "a second file about a totally different subject")
        try:
            organism2.perturb_file(path2)
            assert False, "expected a RuntimeError for the dimension mismatch"
        except RuntimeError as exc:
            assert "64" in str(exc) and "128" in str(exc)
        organism2.stop()


def test_preexisting_database_without_a_recorded_baseline_is_backfilled_on_restore():
    """Simulates a database ingested by code from before this guard existed:
    chunks are on disk, but meta["embedding_dim"] was never written. The
    guard must backfill it from the existing data on restore, not silently
    adopt whatever dimension shows up next as the new baseline."""
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        path = _write(td, "a.txt", "some corpus text about territories and homeostasis")
        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        organism.perturb_file(path)
        # Erase the baseline to simulate a pre-guard database.
        organism.storage.set_meta("embedding_dim", None)
        organism.stop()

        organism2 = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        assert organism2.storage.get_meta("embedding_dim") == "64", (
            "restoring an existing database must backfill the dimension baseline "
            "from its actual stored chunks, not leave it unset"
        )

        # Now prove the backfilled baseline actually protects: a real
        # mismatch on this "pre-existing" database must still be caught.
        organism2.stop()
        organism3 = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=999))
        path2 = _write(td, "b.txt", "a different file entirely, unrelated content")
        try:
            organism3.perturb_file(path2)
            assert False, "expected a RuntimeError for the dimension mismatch"
        except RuntimeError:
            pass
        organism3.stop()


def test_legacy_database_binds_model_identity_on_recovered_writer_start():
    import sqlite3

    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "legacy.db")
        organism = Organism(OrganismConfig(db_path=db), HashEmbedder(dim=16))
        path = os.path.join(td, "source.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("legacy model identity test")
        organism.perturb_file(path)
        organism.stop()

        con = sqlite3.connect(db)
        con.execute("DELETE FROM meta WHERE key='embedding_model'")
        con.commit()
        con.close()

        restored = Organism(OrganismConfig(db_path=db), HashEmbedder(dim=16))
        assert restored.storage.get_meta("embedding_model") == "hash:16"
        assert any(event[2] == "legacy_embedding_model_bound" for event in restored.storage.recent_events())
        restored.stop()
