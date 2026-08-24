"""Regression mesh for the watch-folder / background-daemon failure closure
raised when a user pointed --watch at a folder that didn't exist and got
silence instead of an error. Each test here corresponds to one failure
class identified while tracing the full watch -> perturb -> ingest path,
not just the one symptom that was reported.
"""
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vll_organism.embedder import EmbeddingError, HashEmbedder
from vll_organism.organism import Organism, OrganismConfig, ensure_watch_folder
from vll_organism.storage import Storage


def _write(dirpath, name, content, mode="w"):
    path = os.path.join(dirpath, name)
    with open(path, mode) as f:
        f.write(content)
    return path


class FlakyEmbedder:
    """Raises an UNEXPECTED (non-EmbeddingError) exception for text
    containing `fail_on_substring` -- simulates a bug/crash in the
    embedding path, distinct from a normal EmbeddingError, to prove
    scan_watch_folder isolates a single file's failure from the rest of
    the pass rather than letting it propagate and kill the watcher."""

    def __init__(self, dim=64, fail_on_substring=None):
        self._base = HashEmbedder(dim=dim)
        self.dim = dim
        self.fail_on_substring = fail_on_substring

    def embed(self, text):
        if self.fail_on_substring and self.fail_on_substring in text:
            raise RuntimeError("simulated unexpected failure (not EmbeddingError)")
        return self._base.embed(text)


class OnceFailingEmbedder:
    """Raises EmbeddingError (the normal, expected failure type) on the
    first call only, then works -- simulates Ollama not being up yet when
    the first scan happens."""

    def __init__(self, dim=64):
        self._base = HashEmbedder(dim=dim)
        self.dim = dim
        self.attempts = 0

    def embed(self, text):
        self.attempts += 1
        if self.attempts == 1:
            raise EmbeddingError("simulated transient failure")
        return self._base.embed(text)


# ---------------- ensure_watch_folder ----------------

def test_ensure_watch_folder_creates_missing_directory():
    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "corpus_drop")
        assert not os.path.exists(target)
        resolved = ensure_watch_folder(target)
        assert os.path.isdir(target)
        assert resolved == os.path.abspath(target)


def test_ensure_watch_folder_creates_nested_missing_directories():
    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "a", "b", "corpus_drop")
        ensure_watch_folder(target)
        assert os.path.isdir(target)


def test_ensure_watch_folder_raises_on_conflicting_file():
    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "not_a_folder")
        _write(td, "not_a_folder", "this is a file, not a directory")
        try:
            ensure_watch_folder(target)
            assert False, "expected NotADirectoryError"
        except NotADirectoryError as exc:
            assert "not a directory" in str(exc)


def test_ensure_watch_folder_idempotent_on_existing_directory():
    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "already_there")
        os.makedirs(target)
        # Should not raise, should not complain, on a folder that's already there.
        resolved = ensure_watch_folder(target)
        assert resolved == os.path.abspath(target)


# ---------------- Organism.start_watching ----------------

def test_start_watching_raises_before_spawning_thread_on_conflict():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        conflict = _write(td, "conflict", "a file")
        try:
            organism.start_watching(conflict)
            assert False, "expected NotADirectoryError"
        except NotADirectoryError:
            pass
        assert organism._watch_thread is None  # nothing was spawned
        organism.stop()


def test_start_watching_reports_resolved_path_in_status():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        watch_dir = os.path.join(td, "corpus_drop")
        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        resolved = organism.start_watching(watch_dir, poll_interval_s=0.05)
        assert organism.status()["watch_folder_path"] == resolved
        organism.stop()


# ---------------- scan_watch_folder: missing folder ----------------

def test_scan_watch_folder_missing_sets_flag_no_crash():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        missing = os.path.join(td, "does_not_exist")
        count = organism.scan_watch_folder(missing)  # bypasses start_watching's validation on purpose
        assert count == 0
        assert organism.watch_folder_ok is False
        organism.stop()


def test_scan_watch_folder_recovers_when_folder_appears():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        watch_dir = os.path.join(td, "corpus_drop")

        organism.scan_watch_folder(watch_dir)
        assert organism.watch_folder_ok is False

        os.makedirs(watch_dir)
        _write(watch_dir, "note.txt", "some corpus text about territories and homeostasis")
        n = organism.scan_watch_folder(watch_dir)
        assert organism.watch_folder_ok is True
        assert n == 1
        organism.stop()


# ---------------- scan_watch_folder: per-file crash isolation ----------------

def test_scan_watch_folder_isolates_one_bad_file_from_the_rest():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        watch_dir = os.path.join(td, "corpus_drop")
        os.makedirs(watch_dir)
        _write(watch_dir, "bad.txt", "TRIGGER this file's embedding will explode")
        _write(watch_dir, "good.txt", "this file should still be ingested normally")

        embedder = FlakyEmbedder(dim=64, fail_on_substring="TRIGGER")
        organism = Organism(OrganismConfig(db_path=db_path), embedder)

        count = organism.scan_watch_folder(watch_dir)  # must not raise
        assert count == 1  # only good.txt succeeded
        assert organism.storage.chunk_count() == 1

        # bad.txt must NOT be marked known -- it should be retried, not
        # silently, permanently dropped because of an unexpected exception.
        bad_path = os.path.join(watch_dir, "bad.txt")
        assert bad_path not in organism._known_files
        organism.stop()


# ---------------- perturb_file / scan_watch_folder: retry semantics ----------------

def test_transient_embedding_failure_is_retried_next_scan():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        watch_dir = os.path.join(td, "corpus_drop")
        os.makedirs(watch_dir)
        _write(watch_dir, "note.txt", "content that will fail to embed on the first attempt only")

        embedder = OnceFailingEmbedder(dim=64)
        organism = Organism(OrganismConfig(db_path=db_path), embedder)

        first = organism.scan_watch_folder(watch_dir)
        assert first == 0
        assert organism.storage.chunk_count() == 0
        note_path = os.path.join(watch_dir, "note.txt")
        assert note_path not in organism._known_files, (
            "a file whose embedding failed must not be marked known, or it "
            "will never be retried even after the transient condition clears"
        )

        second = organism.scan_watch_folder(watch_dir)  # mtime unchanged, embedder now works
        assert second == 1
        assert organism.storage.chunk_count() == 1
        assert note_path in organism._known_files
        organism.stop()


def test_read_failure_is_retried_instead_of_becoming_false_success(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        watch_dir = os.path.join(td, "corpus_drop")
        os.makedirs(watch_dir)
        target = _write(watch_dir, "temporarily_unreadable.txt", "content")

        import vll_organism.organism as organism_module
        original = organism_module.read_corpus_file
        attempts = {"count": 0}

        def flaky(path):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise OSError("simulated transient read failure")
            return original(path)

        monkeypatch.setattr(organism_module, "read_corpus_file", flaky)
        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        assert organism.scan_watch_folder(watch_dir) == 0
        assert target not in organism._known_files
        assert organism.scan_watch_folder(watch_dir) == 1
        assert target in organism._known_files
        organism.stop()


def test_empty_file_does_not_crash_and_is_marked_known():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        watch_dir = os.path.join(td, "corpus_drop")
        os.makedirs(watch_dir)
        _write(watch_dir, "empty.txt", "")

        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        count = organism.scan_watch_folder(watch_dir)  # must not raise
        assert count == 0
        assert organism.storage.chunk_count() == 0
        empty_path = os.path.join(watch_dir, "empty.txt")
        assert empty_path in organism._known_files
        organism.stop()


# ---------------- tick loop resilience ----------------

def test_tick_once_safely_survives_idle_tick_exception(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))

        def boom():
            raise RuntimeError("simulated tick failure")
        monkeypatch.setattr(organism, "idle_tick", boom)

        organism._tick_once_safely()  # must not raise
        organism.stop()


# ---------------- Storage: db parent directory ----------------

def test_storage_creates_missing_parent_directory():
    with tempfile.TemporaryDirectory() as td:
        nested = os.path.join(td, "some", "nested", "path", "organism.db")
        assert not os.path.isdir(os.path.dirname(nested))
        store = Storage(nested)
        assert os.path.isfile(nested)
        store.close()


def test_binary_file_is_rejected_and_retried_not_ingested_as_latin1_garbage():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        watch_dir = os.path.join(td, "corpus_drop")
        os.makedirs(watch_dir)
        path = os.path.join(watch_dir, "binary.bin")
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 100)
        organism = Organism(OrganismConfig(db_path=db_path), HashEmbedder(dim=64))
        assert organism.scan_watch_folder(watch_dir) == 0
        assert organism.storage.chunk_count() == 0
        assert path not in organism._known_files
        organism.stop()
