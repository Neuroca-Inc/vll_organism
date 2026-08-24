"""Embedding backends for the persistent knowledge organism.

OllamaEmbedder is the production boundary. HashEmbedder is a deterministic
test-only backend used to exercise ingestion, territory assignment, graph
construction, recovery, and retrieval without a running external service.

The application owns chunking and context-rejection recovery; the production
backend therefore requests ``truncate=false`` so the provider cannot silently
discard source content.
"""
from __future__ import annotations

import hashlib
import math
from typing import List, Optional, Protocol

import requests


class Embedder(Protocol):
    """Minimal embedding interface the rest of the system depends on."""

    dim: int
    model_id: str

    def embed(self, text: str) -> List[float]:
        ...


class EmbeddingError(RuntimeError):
    """Raised when an embedding backend fails to produce a vector."""


class EmbeddingInputTooLongError(EmbeddingError):
    """Raised when the backend rejects a chunk specifically for exceeding
    the model's context window (as opposed to a network/server problem).

    This matters because it changes what "retry" means: a network blip is
    transient -- the same chunk will probably succeed next scan. A chunk
    that's structurally too long for the model will fail identically every
    time it's retried unchanged. Callers should split the text and retry
    the pieces, not just requeue the same request."""


class OllamaEmbedder:
    """Production embedder using Ollama's current ``/api/embed`` API."""

    def __init__(self, model: str = "all-minilm", base_url: str = "http://127.0.0.1:11434",
                 dim: int = 384, timeout: float = 120.0, num_ctx: Optional[int] = None):
        self.model = model
        self.model_id = f"ollama:{model}"
        self.base_url = base_url.rstrip("/")
        self.dim = int(dim)
        self.timeout = float(timeout)
        self.num_ctx = num_ctx

    def embed(self, text: str) -> List[float]:
        url = f"{self.base_url}/api/embed"
        # truncate=False is deliberate. Silent truncation would make the
        # embedding succeed while discarding part of a corpus chunk, bypassing
        # Organism._embed_with_split_retry and corrupting semantic coverage.
        payload = {"model": self.model, "input": text, "truncate": False}
        if self.num_ctx is not None:
            payload["options"] = {"num_ctx": self.num_ctx}
        try:
            resp = requests.post(url, json=payload, timeout=(5.0, self.timeout))
        except requests.Timeout as exc:
            raise EmbeddingError(
                f"Ollama embedding request timed out after {self.timeout:.1f}s "
                f"(model={self.model!r}, url={url!r}). The model may still be loading or "
                f"CPU inference may simply need a larger --embed-timeout."
            ) from exc
        except requests.RequestException as exc:
            raise EmbeddingError(
                f"Ollama embedding request failed (model={self.model!r}, url={url!r}): {exc}. "
                f"Is Ollama running locally and has '{self.model}' been pulled?"
            ) from exc

        if not resp.ok:
            body = resp.text.strip()
            lower = body.lower()
            if "context length" in lower or "input length" in lower or "too long" in lower:
                raise EmbeddingInputTooLongError(
                    f"Model {self.model!r} rejected a {len(text)}-char chunk as exceeding "
                    f"its context window: {body}"
                )
            raise EmbeddingError(
                f"Ollama embedding request failed (model={self.model!r}, url={url!r}): "
                f"{resp.status_code} {resp.reason}: {body}"
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise EmbeddingError(
                f"Ollama returned a non-JSON embedding response for model {self.model!r}: "
                f"{resp.text[:500]!r}"
            ) from exc
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or not vectors or not isinstance(vectors[0], list) or not vectors[0]:
            raise EmbeddingError(f"Ollama returned no embedding for model {self.model!r}: {data!r}")
        return [float(x) for x in vectors[0]]

    def probe_dim(self) -> int:
        """Actually call the model once to learn its real output dimension."""
        vec = self.embed("dimension_probe")
        self.dim = len(vec)
        return self.dim


class HashEmbedder:
    """Deterministic, dependency-free pseudo-embedding for tests only.

    Uses feature hashing over word tokens: every token's hash bucket gets a
    +1/-1 (sign chosen by a second hash) contribution, then the vector is
    L2-normalized. This gives reproducible, non-degenerate vectors with
    genuine (if crude) token-overlap similarity structure, which is enough
    to exercise territory assignment, edge creation, and the homeostasis
    loop end to end without any network dependency.

    NOT a production embedder -- it has no real semantics. cli.py refuses to
    start the organism with this backend unless --allow-test-embedder is set.
    """

    def __init__(self, dim: int = 128):
        self.dim = int(dim)
        self.model_id = f"hash:{self.dim}"

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        tokens = text.lower().split()
        if not tokens:
            tokens = [""]
        for tok in tokens:
            h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "big") % self.dim
            sign = 1.0 if (h[4] & 1) == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        if norm <= 0.0:
            vec[0] = 1.0
            return vec
        return [x / norm for x in vec]


__all__ = ["Embedder", "EmbeddingError", "EmbeddingInputTooLongError", "OllamaEmbedder", "HashEmbedder"]
