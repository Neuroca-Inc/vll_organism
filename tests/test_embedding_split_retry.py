"""Regression mesh for a real failure a user hit running the actual daemon
against Ollama: a corpus file where the embedding model ("all-minilm")
rejected every chunk with a 500 "input length exceeds the context length"
error. Before this fix, that failure was indistinguishable from a
transient one (Ollama down), so `had_embedding_failure=True` kept the
file's mtime unmarked and it was retried -- identically, forever -- on
every single poll interval, hammering the embedder with guaranteed-failing
requests and never actually ingesting the file.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vll_organism.embedder import EmbeddingInputTooLongError, HashEmbedder
from vll_organism.ingest import embed_with_split_retry
from vll_organism.organism import Organism, OrganismConfig


def _write(dirpath, name, content):
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TooLongEmbedder:
    """Simulates Ollama's real behavior for an input that exceeds the
    model's context window: rejects anything over `limit` chars with the
    same exception type OllamaEmbedder now raises for that specific
    server-reported reason, embeds normally otherwise."""

    def __init__(self, limit, dim=64):
        self._base = HashEmbedder(dim=dim)
        self.dim = dim
        self.limit = limit
        self.calls = 0

    def embed(self, text):
        self.calls += 1
        if len(text) > self.limit:
            raise EmbeddingInputTooLongError(
                f"simulated: {len(text)} chars exceeds this fake model's {self.limit}-char context"
            )
        return self._base.embed(text)


# Long enough that a single chunk_text() chunk will exceed a small fake
# context limit, but with real sentence boundaries throughout so splitting
# can actually make progress.
_LONG_PROSE = " ".join(
    f"This is sentence number {i} in a long paragraph about territories and homeostasis."
    for i in range(40)
)


def test_oversized_chunk_is_split_and_embedded_successfully():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        path = _write(td, "big.txt", _LONG_PROSE)

        embedder = TooLongEmbedder(limit=300)
        organism = Organism(OrganismConfig(db_path=db_path, max_chunk_chars=2000), embedder)

        result = organism.perturb_file(path)
        assert result.had_embedding_failure is False
        assert result.new_chunks > 1, "the oversized chunk should have been split into multiple pieces"
        assert organism.storage.chunk_count() == result.new_chunks

        for rec in organism.storage.all_chunks():
            assert len(rec.text) <= 300, "every stored piece must actually fit the model's limit"
        organism.stop()


def test_content_that_never_fits_is_skipped_permanently_not_retried_forever():
    """limit=10 is below _MIN_SPLIT_CHARS (80), so every fragment hits the
    split floor and is genuinely unembeddable -- the realistic case is a
    pathologically small context window or a chunk that's one giant
    unbroken run. The file must still be marked known (not retried every
    scan forever) with zero chunks ingested, rather than looping."""
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        watch_dir = os.path.join(td, "corpus_drop")
        os.makedirs(watch_dir)
        path = _write(watch_dir, "unembeddable.txt", _LONG_PROSE)

        embedder = TooLongEmbedder(limit=10)
        organism = Organism(OrganismConfig(db_path=db_path), embedder)

        first = organism.scan_watch_folder(watch_dir)
        assert first == 0
        assert organism.storage.chunk_count() == 0
        # Permanent model/input incompatibility is reported once, then the
        # watcher marks the unchanged file known rather than hammering Ollama.
        assert path in organism._known_files, (
            "a chunk that can never fit the model's context window must not be retried "
            "every poll forever -- it should be marked known after one attempt, just like "
            "a permanent read failure"
        )

        calls_after_first_scan = embedder.calls
        second = organism.scan_watch_folder(watch_dir)
        assert second == 0
        assert embedder.calls == calls_after_first_scan, (
            "file was already marked known -- it must not be re-embedded on the next poll"
        )
        organism.stop()


def test_one_pathological_fragment_does_not_sink_its_well_formed_siblings():
    """A paragraph with normal sentences plus one giant unbroken run must
    be recursively hard-wrapped until every fragment embeds.  Partial success
    is not allowed to silently discard the pathological sibling."""
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "organism.db")
        giant_run = "x" * 5000  # no '.', '!', or '?' -- can't be split further
        text = _LONG_PROSE + " " + giant_run
        path = _write(td, "mixed.txt", text)

        embedder = TooLongEmbedder(limit=300)
        organism = Organism(OrganismConfig(db_path=db_path, max_chunk_chars=8000), embedder)

        result = organism.perturb_file(path)
        assert result.new_chunks > 1, "the well-formed sentences should still be ingested"
        stored_text = " ".join(rec.text for rec in organism.storage.all_chunks())
        assert "sentence number 0" in stored_text
        assert "sentence number 39" in stored_text
        assert "x" * 200 in stored_text
        assert result.permanent_failures == 0
        assert result.had_embedding_failure is False
        organism.stop()


def test_recursive_embedding_split_covers_entire_unbroken_fragment():
    """Exact-content dedup may collapse identical descendants in durable
    storage, but the embedding-recovery layer itself must never omit bytes from
    a rejected logical fragment."""
    with tempfile.TemporaryDirectory() as td:
        organism = Organism(
            OrganismConfig(db_path=os.path.join(td, "organism.db")),
            TooLongEmbedder(limit=300),
        )
        giant_run = "x" * 5000
        pieces = embed_with_split_retry(organism.embedder, giant_run)
        assert "".join(text for text, _embedding in pieces) == giant_run
        assert all(len(text) <= 300 for text, _embedding in pieces)
        organism.stop()


def test_long_single_sentence_split_never_truncates_content():
    from vll_organism.ingest import split_long_text

    text = "x" * 5003
    pieces = split_long_text(text, 400)
    assert all(0 < len(piece) <= 400 for piece in pieces)
    assert "".join(pieces) == text
