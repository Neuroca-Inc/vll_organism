"""Deterministic text ingestion and chunking for the organism."""
from __future__ import annotations

import codecs
import hashlib
import re
from dataclasses import dataclass
from typing import List, Tuple

from .embedder import Embedder, EmbeddingInputTooLongError


MIN_SPLIT_CHARS = 80


@dataclass(frozen=True)
class Chunk:
    text: str
    hash: str
    index: int


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_corpus_file(path: str) -> str:
    """Read textual corpus data without silently decoding arbitrary binaries."""
    with open(path, "rb") as handle:
        data = handle.read()
    if not data:
        return ""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    if _looks_binary(data):
        raise ValueError(f"{path!r} appears to be binary, not corpus text")
    return data.decode("latin-1")


def _looks_binary(data: bytes) -> bool:
    sample = data[:8192]
    if b"\x00" in sample:
        return True
    allowed_controls = {8, 9, 10, 12, 13}
    controls = sum(byte < 32 and byte not in allowed_controls for byte in sample)
    return controls / max(1, len(sample)) > 0.02


def chunk_text(text: str, max_chars: int = 1500, min_chars: int = 80) -> List[Chunk]:
    """Paragraph-aware, deterministic chunking with no content truncation."""
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    raw_chunks: List[str] = []
    buffer = ""
    for paragraph in paragraphs:
        candidate = paragraph if not buffer else buffer + "\n\n" + paragraph
        if len(candidate) <= max_chars:
            buffer = candidate
            continue
        if buffer:
            raw_chunks.append(buffer)
            buffer = ""
        if len(paragraph) <= max_chars:
            buffer = paragraph
        else:
            raw_chunks.extend(split_long_text(paragraph, max_chars))
    if buffer:
        raw_chunks.append(buffer)

    merged: List[str] = []
    for chunk in raw_chunks:
        if merged and len(chunk) < min_chars and len(merged[-1]) + 2 + len(chunk) <= max_chars:
            merged[-1] += "\n\n" + chunk
        else:
            merged.append(chunk)
    return [Chunk(text=value, hash=sha256_hex(value), index=i) for i, value in enumerate(merged)]


def split_long_text(text: str, max_chars: int) -> List[str]:
    """Split long text at sentence boundaries, hard-wrapping only when needed."""
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: List[str] = []
    buffer = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if buffer:
                out.append(buffer)
                buffer = ""
            parts = (len(sentence) + max_chars - 1) // max_chars
            width = (len(sentence) + parts - 1) // parts
            out.extend(sentence[i : i + width] for i in range(0, len(sentence), width))
            continue
        candidate = sentence if not buffer else buffer + " " + sentence
        if len(candidate) <= max_chars:
            buffer = candidate
        else:
            if buffer:
                out.append(buffer)
            buffer = sentence
    if buffer:
        out.append(buffer)
    return out



def embed_with_split_retry(
    embedder: Embedder, text: str, min_split_chars: int = MIN_SPLIT_CHARS
) -> List[Tuple[str, List[float]]]:
    """Embed all text or raise instead of returning partial semantic coverage.

    Context limits are not reliably known in character units. Rejected
    fragments are therefore bisected deterministically until every descendant
    embeds or the minimum fragment size is reached. Exact duplicate descendants
    may later deduplicate in durable storage, but nothing is dropped here.
    """
    if min_split_chars < 1:
        raise ValueError("min_split_chars must be >= 1")
    pending = [text]
    embedded: List[Tuple[str, List[float]]] = []
    while pending:
        fragment = pending.pop(0)
        try:
            embedded.append((fragment, embedder.embed(fragment)))
            continue
        except EmbeddingInputTooLongError:
            if len(fragment) <= min_split_chars:
                raise
        target = max(min_split_chars, len(fragment) // 2)
        pieces = split_long_text(fragment, target)
        if len(pieces) < 2 or any(not piece for piece in pieces):
            raise EmbeddingInputTooLongError(
                f"could not split rejected {len(fragment)}-char fragment below model context"
            )
        pending[0:0] = pieces
    return embedded

def chunk_id_for(_source_path: str, chunk_hash: str) -> str:
    """Global content-derived identity; provenance is stored separately."""
    return f"chunk:{chunk_hash[:24]}"


__all__ = ["Chunk", "MIN_SPLIT_CHARS", "sha256_hex", "read_corpus_file", "chunk_text", "chunk_id_for", "split_long_text", "embed_with_split_retry"]
