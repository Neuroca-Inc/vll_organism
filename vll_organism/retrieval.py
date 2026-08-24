"""Bounded semantic retrieval over territories plus sparse graph expansion."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Set

from .dynamics import KnowledgeDynamics, cosine_similarity
from .graph import KnowledgeGraph
from .storage import ChunkRecord, Storage


@dataclass(frozen=True)
class QueryConfig:
    top_k: int = 8
    territory_k: int = 3
    candidate_cap: int = 256
    graph_hops: int = 1
    graph_candidate_cap: int = 128
    dynamics_weight: float = 0.05

    def validate(self) -> None:
        if self.top_k < 1:
            raise ValueError("top_k must be >= 1")
        if self.territory_k < 1:
            raise ValueError("territory_k must be >= 1")
        if self.candidate_cap < self.top_k:
            raise ValueError("candidate_cap must be >= top_k")
        if self.graph_hops < 0:
            raise ValueError("graph_hops must be >= 0")
        if self.graph_candidate_cap < 0:
            raise ValueError("graph_candidate_cap must be >= 0")
        if not 0.0 <= self.dynamics_weight <= 0.25:
            raise ValueError("dynamics_weight must be in [0, 0.25]")


@dataclass(frozen=True)
class QueryHit:
    chunk_id: str
    text: str
    sources: tuple[str, ...]
    territory: int | None
    semantic_similarity: float
    score: float


class RetrievalEngine:
    def __init__(
        self, storage: Storage, dynamics: KnowledgeDynamics, graph: KnowledgeGraph | None = None
    ):
        self.storage = storage
        self.dynamics = dynamics
        self._neighbor_provider = graph.weighted_neighbors if graph is not None else storage.weighted_neighbors

    def query(self, embedding: Sequence[float], config: QueryConfig | None = None) -> List[QueryHit]:
        cfg = config or QueryConfig()
        cfg.validate()
        nearest = self.dynamics.nearest_territories(embedding, cfg.territory_k)
        territories = [territory for territory, _ in nearest]
        records = self.storage.candidate_chunks(territories, cfg.candidate_cap)
        scored = self._score_records(embedding, records, cfg.dynamics_weight)

        if cfg.graph_hops and cfg.graph_candidate_cap and scored:
            seeds = [record.id for _, _, record in scored[: max(cfg.top_k, 8)]]
            expanded_ids = self._expand_graph(seeds, cfg.graph_hops, cfg.graph_candidate_cap)
            known = {record.id for _, _, record in scored}
            new_ids = [chunk_id for chunk_id in expanded_ids if chunk_id not in known]
            if new_ids:
                scored.extend(
                    self._score_records(
                        embedding,
                        self.storage.get_chunks(new_ids),
                        cfg.dynamics_weight,
                    )
                )

        best: Dict[str, tuple[float, float, ChunkRecord]] = {}
        for score, semantic, record in scored:
            current = best.get(record.id)
            if current is None or score > current[0]:
                best[record.id] = (score, semantic, record)
        ranked = sorted(best.values(), key=lambda row: row[0], reverse=True)[: cfg.top_k]
        return [
            QueryHit(
                chunk_id=record.id,
                text=record.text,
                sources=record.sources or ((record.source,) if record.source else ()),
                territory=record.territory,
                semantic_similarity=semantic,
                score=score,
            )
            for score, semantic, record in ranked
        ]

    def _score_records(
        self,
        embedding: Sequence[float],
        records: Sequence[ChunkRecord],
        dynamics_weight: float,
    ) -> List[tuple[float, float, ChunkRecord]]:
        out: List[tuple[float, float, ChunkRecord]] = []
        semantic_weight = 1.0 - dynamics_weight
        for record in records:
            if record.embedding is None:
                continue
            semantic = cosine_similarity(embedding, record.embedding)
            dynamic = self.dynamics.state_score(record.id)
            score = semantic_weight * semantic + dynamics_weight * dynamic
            out.append((score, semantic, record))
        out.sort(key=lambda row: row[0], reverse=True)
        return out

    def _expand_graph(self, seeds: Sequence[str], hops: int, cap: int) -> List[str]:
        visited: Set[str] = set(seeds)
        frontier = list(seeds)
        added: List[str] = []
        for _ in range(hops):
            if not frontier or len(added) >= cap:
                break
            next_frontier: List[str] = []
            for node in frontier:
                for neighbor, _weight in self._neighbor_provider(node, 12):
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    added.append(neighbor)
                    next_frontier.append(neighbor)
                    if len(added) >= cap:
                        break
                if len(added) >= cap:
                    break
            frontier = next_frontier
        return added


__all__ = ["QueryConfig", "QueryHit", "RetrievalEngine"]
